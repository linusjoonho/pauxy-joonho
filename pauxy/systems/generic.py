import ast
import h5py
import numpy
import sys
import scipy.linalg
import time
from scipy.sparse import csr_matrix
from pauxy.utils.linalg import modified_cholesky
from pauxy.utils.io import (
        from_qmcpack_sparse,
        from_qmcpack_dense,
        write_qmcpack_sparse,
        write_qmcpack_dense,
        )
from pauxy.estimators.generic import (
        local_energy_generic, core_contribution,
        local_energy_generic_cholesky, core_contribution_cholesky
)
from pauxy.utils.mpi import get_shared_array, have_shared_mem


class Generic(object):
    """Generic system defined by ab-initio Hamiltonian.

    Can be created by either passing the one and two electron integrals directly
    or initialised from integrals stored in QMCPACK hdf5 format. If initialising
    from file the `inputs' optional dictionary should be populated.

    Parameters
    ----------
    nelec : tuple
        Number of alpha and beta electrons.
    h1e : :class:`numpy.ndarray'
        One-electron integrals. Optional. Default: None.
    chol : :class:`numpy.ndarray'
        Factorized 2-electron integrals (L_{ik,n}) of shape (nbasis^2, nchol).
        Optional. Default: None.
    ecore : float
        Core energy.
    inputs : dict
        Input options defined below.
    nup : int
        Number of up electrons.
    ndown : int
        Number of down electrons.
    integrals : string
        Path to file containing one- and two-electron integrals in QMCPACK
        format.
    verbose : bool
        Print extra information.

    Attributes
    ----------
    H1 : :class:`numpy.ndarray`
        One-body part of the Hamiltonian. Spin-dependent by default.
    ecore : float
        Core contribution to the total energy.
    h1e_mod : :class:`numpy.ndarray`
        Modified one-body Hamiltonian.
    chol_vecs : :class:`numpy.ndarray`
        Cholesky vectors. [M^2, nchol]
    nchol : int
        Number of cholesky vectors.
    nfields : int
        Number of auxiliary fields required.
    sparse_cutoff : float
        Screen out integrals below this threshold. Optional. Default 0.
    cplx_chol : bool
        Force setting of interpretation of cholesky decomposition. Optional.
        Default False, i.e. real/complex factorization determined from cholesky
        integrals.
    """

    def __init__(self, nelec=None, h1e=None, chol=None, ecore=None, h1e_mod=None,
                 mu=None, verbose=False, write_ints=False):
        if verbose:
            print("# Parsing input options.")
        self.name = "Generic"
        self.verbose = verbose
        self.nup, self.ndown = nelec
        self.nelec = nelec
        self.ne = self.nup + self.ndown
        self.mu = mu
        self.ecore = ecore
        self.chol_vecs = chol
        if isinstance(self.chol_vecs.dtype, numpy.complex128):
            if verbose:
                print("# Found complex integrals.")
                print("# Using Hermitian Cholesky decomposition.")
        else:
            if verbose:
                print("# Using real Cholesky decomposition.")
            self.cplx_chol = False
        self.H1 = h1e
        self.nbasis = h1e.shape[-1]
        self._alt_convention = False
        mem = self.chol_vecs.nbytes / (1024.0**3)
        self.sparse = False
        if verbose:
            print("# Number of orbitals: %d"%self.nbasis)
            print("# Number of electrons: (%d, %d)"%(self.nup, self.ndown))
            print("# Approximate memory required by Cholesky vectors %f GB"%mem)
        self.nchol = self.chol_vecs.shape[-1]
        if h1e_mod is not None:
            self.h1e_mod = h1e_mod
        else:
            h1e_mod = numpy.zeros(self.H1.shape, dtype=self.H1.dtype)
            construct_h1e_mod(chol, self.H1, h1e_mod)
            self.h1e_mod = h1e_mod
        self.ktwist = numpy.array([None])
        # For consistency
        self.vol = 1.0
        self.hs_pot = self.chol_vecs
        self.nfields = self.nchol
        if verbose:
            print("# Number of Cholesky vectors: %d"%(self.nchol))
            print("# Number of fields: %d"%(self.nfields))
        if write_ints:
            self.write_integrals()
        if verbose:
            print("# Finished setting up Generic system object.")

    # def construct_integral_tensors_real(self, trial):
        # # Half rotated cholesky vectors (by trial wavefunction).
        # # Assuming nup = ndown here
        # M = self.nbasis
        # na = self.nup
        # nb = self.ndown
        # if self.verbose:
            # print("# Constructing half rotated Cholesky vectors.")
        # # rup = numpy.zeros(shape=(self.nchol, na, M),
                          # # dtype=numpy.complex128)
        # # rdn = numpy.zeros(shape=(self.nchol, nb, M),
                          # # dtype=numpy.complex128)
        # if self.sparse:
            # self.hs_pot = self.hs_pot.toarray().reshape(M,M,self.nfields)
        # else:
            # self.hs_pot = self.hs_pot.reshape(M,M,self.nfields)
        # start = time.time()
        # # rrup = numpy.einsum('ia,ikn->akn',
                           # # trial.psi[:,:na].conj(),
                           # # self.hs_pot,
                           # # optimize='greedy')
        # # rdn = numpy.einsum('ia,ikn->akn',
                           # # trial.psi[:,na:].conj(),
                           # # self.hs_pot,
                           # # optimize='greedy')
        # rup = numpy.tensordot(trial.psi[:,:na].conj(),
                              # self.hs_pot,
                              # axes=((0),(0)))
        # rdn = numpy.tensordot(trial.psi[:,na:].conj(),
                              # self.hs_pot,
                              # axes=((0),(0)))
        # trot = time.time() - start
        # # This is much faster than einsum.
        # # for l in range(self.nchol):
            # # rup[l] = numpy.dot(trial.psi[:,:na].conj().T, self.chol_vecs[l])
            # # rdn[l] = numpy.dot(trial.psi[:,na:].conj().T, self.chol_vecs[l])
        # if self.half_rotated_integrals:
            # start = time.time()
            # if self.verbose:
                # print("# Constructing half rotated V_{(ab)(kl)}.")
            # vakbl_a = (numpy.einsum('akn,bln->akbl', rup, rup, optimize='greedy') -
                       # numpy.einsum('bkn,aln->akbl', rup, rup, optimize='greedy'))
            # vakbl_b = (numpy.einsum('akn,bln->akbl', rdn, rdn, optimize='greedy') -
                       # numpy.einsum('bkn,aln->akbl', rdn, rdn, optimize='greedy'))
            # tvakbl = time.time() - start
        # if self.cutoff is not None:
            # rup[numpy.abs(rup) < self.cutoff] = 0.0
            # rdn[numpy.abs(rdn) < self.cutoff] = 0.0
            # if self.half_rotated_integrals:
                # vakbl_a[numpy.abs(vakbl_a) < self.cutoff] = 0.0
                # vakbl_b[numpy.abs(vakbl_b) < self.cutoff] = 0.0
        # if self.half_rotated_integrals:
            # self.vakbl = [csr_matrix(vakbl_a.reshape((M*na, M*na))),
                          # csr_matrix(vakbl_b.reshape((M*nb, M*nb)))]
        # if self.sparse:
            # if self.cutoff is not None:
                # self.hs_pot[numpy.abs(self.hs_pot) < self.cutoff] = 0
            # self.hs_pot = self.hs_pot.reshape((M*M,-1))
            # self.hs_pot = csr_matrix(self.hs_pot)
            # self.rot_hs_pot = [csr_matrix(rup.reshape((M*na, -1))),
                               # csr_matrix(rdn.reshape((M*nb, -1)))]
        # else:
            # self.rot_hs_pot = [rup.reshape((M*na, -1)), rdn.reshape((M*nb, -1))]
            # self.hs_pot = self.hs_pot.reshape((M*M,-1))
        # self.rchol_vecs = self.rot_hs_pot
        # if self.verbose:
            # print("# Time to construct half-rotated Cholesky: %f s"%trot)
            # if self.sparse:
                # nnz = self.rchol_vecs[0].nnz
                # print("# Number of non-zero elements in rotated cholesky: %d"%nnz)
                # nelem = self.rchol_vecs[0].shape[0] * self.rchol_vecs[0].shape[1]
                # print("# Sparsity: %f"%(1-float(nnz)/nelem))
                # mem = (2*nnz*16/(1024.0**3))
            # else:
                # mem = self.rchol_vecs[0].nbytes + self.rchol_vecs[1].nbytes
                # mem /= 1024.0**3
            # print("# Approximate memory required for half-rotated Cholesky: "
                  # "{:.6f} GB".format(mem))
            # if self.half_rotated_integrals:
                # print("# Time to construct V_{(ak)(bl)}: %f"%tvakbl)
                # nnz = self.vakbl[0].nnz
                # print("# Number of non-zero elements in V_{(ak)(bl)}: %d"%nnz)
                # mem = (2*nnz*16/(1024.0**3))
                # print("# Approximate memory used %f GB"%mem)
                # nelem = self.vakbl[0].shape[0] * self.vakbl[0].shape[1]
                # print("# Sparsity: %f"%(1-float(nnz)/nelem))

    # def construct_integral_tensors_cplx(self, trial):
        # # Half rotated cholesky vectors (by trial wavefunction).
        # # Assuming nup = ndown here
        # M = self.nbasis
        # na = self.nup
        # nb = self.ndown
        # if self.verbose:
            # print("# Constructing complex half rotated HS Potentials.")
        # rup = numpy.zeros(shape=(self.nfields, na, M),
                          # dtype=numpy.complex128)
        # rdn = numpy.zeros(shape=(self.nfields, nb, M),
                          # dtype=numpy.complex128)
        # # rup = numpy.einsum('ia,lik->lak',
                           # # trial.psi[:,:na].conj(),
                           # # self.hs_pot)
        # # rdn = numpy.einsum('ia,lik->lak',
                           # # trial.psi[:,na:].conj(),
                           # # self.hs_pot)
        # # This is much faster than einsum.
        # start = time.time()
        # if self.sparse:
            # self.hs_pot = self.hs_pot.toarray().reshape(M,M,self.nfields)
            # self.hs_pot = self.hs_pot.transpose(2,0,1)
        # for (n,cn) in enumerate(self.hs_pot):
            # rup[n] = numpy.dot(trial.psi[:,:na].conj().T, self.hs_pot[n])
            # rdn[n] = numpy.dot(trial.psi[:,na:].conj().T, self.hs_pot[n])
        # self.rot_hs_pot = [csr_matrix(rup.reshape((-1,M*na)).T),
                           # csr_matrix(rdn.reshape((-1,M*nb)).T)]
        # if self.verbose:
            # print("# Time to construct half-rotated HS potentials: "
                  # "%f s"%(time.time()-start))
            # nnz = self.rot_hs_pot[0].nnz
            # print("# Number of non-zero elements in rotated potentials: %d"%nnz)
            # nelem = self.rot_hs_pot[0].shape[0] * self.rot_hs_pot[0].shape[1]
            # print("# Sparsity: %f"%(1-float(nnz)/nelem))
            # mem = (2*nnz*16/(1024.0**3))
            # print("# Approximate memory required %f" " GB"%mem)
            # print("# Constructing half rotated V_{(ab)(kl)}.")
        # # This is also much faster than einsum.
        # Qak = numpy.zeros((self.nchol, M*na), dtype=numpy.complex128)
        # Rbl = numpy.zeros((self.nchol, M*na), dtype=numpy.complex128)
        # start = time.time()
        # for (n,cn) in enumerate(self.chol_vecs):
            # Qak[n] = numpy.dot(trial.psi[:,:na].conj().T, cn).ravel()
            # Rbl[n] = numpy.dot(trial.psi[:,:na].conj().T, cn.conj()).ravel()
        # if self.verbose:
            # print("# Time to construct Qak, Rbl: %f s"%(time.time()-start))
        # Makbl = numpy.dot(Qak.T,Rbl)
        # vakbl_a = (
            # Makbl -
            # Makbl.reshape(na,M,na,M).transpose((2,1,0,3)).reshape(na*M,na*M)
        # )
        # Qak = numpy.zeros((self.nchol, M*nb), dtype=numpy.complex128)
        # Rbl = numpy.zeros((self.nchol, M*nb), dtype=numpy.complex128)
        # for (n,cn) in enumerate(self.chol_vecs):
            # Qak[n] = numpy.dot(trial.psi[:,na:].conj().T, cn).ravel()
            # Rbl[n] = numpy.dot(trial.psi[:,na:].conj().T, cn.conj()).ravel()
        # Makbl = numpy.dot(Qak.T,Rbl)
        # vakbl_b = (
            # Makbl -
            # Makbl.reshape(nb,M,nb,M).transpose((2,1,0,3)).reshape(nb*M,nb*M)
        # )
        # self.vakbl = [csr_matrix(vakbl_a.reshape((M*na, M*na))),
                      # csr_matrix(vakbl_b.reshape((M*nb, M*nb)))]
        # tvakbl = time.time() - start
        # # TODO: Stop converting hs pot to dense
        # if self.sparse:
            # if self.cutoff is not None:
                # self.hs_pot[numpy.abs(self.hs_pot) < self.cutoff] = 0
            # tmp = numpy.transpose(self.hs_pot, axes=(1,2,0))
            # tmp = tmp.reshape(self.nbasis*self.nbasis, self.nfields)
            # self.hs_pot = csr_matrix(tmp)
        # else:
            # self.hs_pot = numpy.transpose(self.hs_pot, axes=(1,2,0))
            # self.hs_pot = self.hs_pot.reshape(self.nbasis*self.nbasis, self.nfields)
        # if self.verbose:
            # print("# Time to construct V_{(ak)(bl)}: %f s"%(tvakbl))
            # nnz = self.vakbl[0].nnz
            # mem = (2*nnz*16/(1024.0**3))
            # print("# Number of non-zero elements in V_{(ak)(bl)}: %d"%nnz)
            # print("# Approximate memory used %f GB"%mem)
            # nelem = self.vakbl[0].shape[0] * self.vakbl[0].shape[1]
            # print("# Sparsity: %f"%(1-float(nnz)/nelem))

    def hijkl(self, i, j, k, l):
        ik = i*self.nbasis + k
        jl = j*self.nbasis + l
        return numpy.dot(self.chol_vecs[ik,:], self.chol_vecs[jl,:])

    def write_integrals(self, filename='hamil.h5'):
        if self.sparse:
            write_qmcpack_sparse(self.H1[0],
                                 self.chol_vecs.reshape((-1,self.nbasis*self.nbasis)).T.copy(),
                                 self.nelec, self.nbasis,
                                 ecuc=self.ecore, filename=filename)
        else:
            write_qmcpack_dense(self.H1[0],
                                self.chol_vecs,
                                self.nelec, self.nbasis,
                                enuc=self.ecore, filename=filename,
                                real_chol=not self.cplx_chol)

def read_integrals(integral_file):
    try:
        (h1e, schol_vecs, ecore, nbasis, nup, ndown) = (
                from_qmcpack_sparse(integral_file)
                )
        chol_vecs = schol_vecs.toarray()
    except KeyError:
        (h1e, chol_vecs, ecore, nbasis, nup, ndown) = (
                from_qmcpack_dense(integral_file)
                )
    except OSError:
        print("# Unknown Hamiltonian file {}.".format(integral_file))
    except:
        print("# Unknown Hamiltonian file format.")
    return h1e, chol_vecs, ecore

def construct_h1e_mod(chol, h1e, h1e_mod):
    # Subtract one-body bit following reordering of 2-body operators.
    # Eqn (17) of [Motta17]_
    nbasis = h1e.shape[-1]
    chol_3 = chol.reshape((nbasis, nbasis, -1))
    # assert chol_3.__array_interface__['data'][0] == chol.__array_interface__['data'][0]
    v0 = 0.5 * numpy.einsum('ikn,jkn->ij', chol_3, chol_3, optimize='optimal')
    h1e_mod[0,:,:] = h1e[0] - v0
    h1e_mod[1,:,:] = h1e[1] - v0
