import copy
import numpy
import scipy.linalg
from pauxy.estimators.mixed import local_energy_multi_det
from pauxy.utils.misc import get_numeric_names

class MultiDetWalker(object):
    """Multi-Det style walker.

    Parameters
    ----------
    weight : int
        Walker weight.
    system : object
        System object.
    trial : object
        Trial wavefunction object.
    index : int
        Element of trial wavefunction to initalise walker to.
    weights : string
        Initialise weights to zeros or ones.
    wfn0 : string
        Initial wavefunction.
    """

    def __init__(self, walker_opts, system, trial, index=0,
                 weights='zeros', verbose=False, nprop_tot=None, nbp=None):
        if verbose:
            print("# Setting up MultiDetWalker object.")
        self.weight = walker_opts.get('weight', 1.0)
        self.unscaled_weight = self.weight
        self.alive = 1
        self.phase = 1 + 0j
        self.nup = system.nup
        self.E_L = 0.0
        self.phi = copy.deepcopy(trial.init)
        self.ndets = trial.psi.shape[0]
        dtype = numpy.complex128
        # This stores an array of overlap matrices with the various elements of
        # the trial wavefunction.
        self.inv_ovlp = [numpy.zeros(shape=(self.ndets, system.nup, system.nup),
                                     dtype=dtype),
                         numpy.zeros(shape=(self.ndets, system.ndown, system.ndown),
                                    dtype=dtype)]
        # TODO: RENAME to something less like weight
        if weights == 'zeros':
            self.weights = numpy.zeros(self.ndets, dtype=dtype)
        else:
            self.weights = numpy.ones(self.ndets, dtype=dtype)
        self.ovlps = numpy.zeros(self.ndets, dtype=dtype)
        # Compute initial overlap. Avoids issues with singular matrices for
        # PHMSD.
        self.ot = self.overlap_direct(trial)
        # TODO: fix name.
        self.ovlp = self.ot
        self.hybrid_energy = 0.0
        if verbose:
            print("# Initial overlap of walker with trial wavefunction: {:13.8e}"
                  .format(self.ot.real))
        # Green's functions for various elements of the trial wavefunction.
        self.Gi = numpy.zeros(shape=(self.ndets, 2, system.nbasis,
                                     system.nbasis), dtype=dtype)
        # Actual green's function contracted over determinant index in Gi above.
        # i.e., <psi_T|c_i^d c_j|phi>
        self.G = numpy.zeros(shape=(2, system.nbasis, system.nbasis),
                             dtype=dtype)
        # Contains overlaps of the current walker with the trial wavefunction.
        self.greens_function(trial)
        self.nb = system.nbasis
        self.nup = system.nup
        self.ndown = system.ndown
        # Historic wavefunction for back propagation.
        self.phi_old = copy.deepcopy(self.phi)
        # Historic wavefunction for ITCF.
        self.phi_init = copy.deepcopy(self.phi)
        # Historic wavefunction for ITCF.
        # self.phi_bp = copy.deepcopy(trial.psi)
        self.buff_names, self.buff_size = get_numeric_names(self.__dict__)
        if nbp is not None:
            self.field_configs = FieldConfig(system.nfields,
                                             nprop_tot, nbp,
                                             numpy.complex128)
        else:
            self.field_configs = None

    def overlap_direct(self, trial):
        nup = self.nup
        for (i, det) in enumerate(trial.psi):
            Oup = numpy.dot(det[:,:nup].conj().T, self.phi[:,:nup])
            Odn = numpy.dot(det[:,nup:].conj().T, self.phi[:,nup:])
            self.ovlps[i] = scipy.linalg.det(Oup) * scipy.linalg.det(Odn)
            if abs(self.ovlps[i]) > 1e-16:
                self.inv_ovlp[0][i] = scipy.linalg.inv(Oup)
                self.inv_ovlp[1][i] = scipy.linalg.inv(Odn)
            self.weights[i] = trial.coeffs[i].conj() * self.ovlps[i]
        return sum(self.weights)

    def inverse_overlap(self, trial):
        """Compute inverse overlap matrix from scratch.

        Parameters
        ----------
        trial : :class:`numpy.ndarray`
            Trial wavefunction.
        """
        nup = self.nup
        for (indx, t) in enumerate(trial.psi):
            Oup = numpy.dot(t[:,:nup].conj().T, self.phi[:,:nup])
            self.inv_ovlp[0][indx,:,:] = scipy.linalg.inv(Oup)
            Odn = numpy.dot(t[:,nup:].conj().T, self.phi[:,nup:])
            self.inv_ovlp[1][indx,:,:] = scipy.linalg.inv(Odn)

    def calc_otrial(self, trial):
        """Caculate overlap with trial wavefunction.

        Parameters
        ----------
        trial : object
            Trial wavefunction object.

        Returns
        -------
        ovlp : float / complex
            Overlap.
        """
        for ix in range(self.ndets):
            det_O_up = 1.0 / scipy.linalg.det(self.inv_ovlp[0][ix])
            det_O_dn = 1.0 / scipy.linalg.det(self.inv_ovlp[1][ix])
            self.ovlps[ix] = det_O_up * det_O_dn
            self.weights[ix] = trial.coeffs[ix].conj() * self.ovlps[ix]
        return sum(self.weights)

    def reortho(self, trial):
        """reorthogonalise walker.

        parameters
        ----------
        trial : object
            trial wavefunction object. for interface consistency.
        """
        nup = self.nup
        ndown = self.ndown
        (self.phi[:,:nup], Rup) = scipy.linalg.qr(self.phi[:,:nup],
                                                  mode='economic')
        Rdown = numpy.zeros(Rup.shape)
        if ndown > 0:
            (self.phi[:,nup:], Rdown) = scipy.linalg.qr(self.phi[:,nup:],
                                                        mode='economic')
        signs_up = numpy.diag(numpy.sign(numpy.diag(Rup)))
        if (ndown > 0):
            signs_down = numpy.diag(numpy.sign(numpy.diag(Rdown)))
        self.phi[:,:nup] = self.phi[:,:nup].dot(signs_up)
        if (ndown > 0):
            self.phi[:,nup:] = self.phi[:,nup:].dot(signs_down)
        drup = scipy.linalg.det(signs_up.dot(Rup))
        drdn = 1.0
        if (ndown > 0):
            drdn = scipy.linalg.det(signs_down.dot(Rdown))
        detR = drup * drdn
        self.ot = self.ot / detR
        return detR

    def greens_function(self, trial):
        """Compute walker's green's function.

        Parameters
        ----------
        trial : object
            Trial wavefunction object.
        """
        nup = self.nup
        for (ix, t) in enumerate(trial.psi):
            # construct "local" green's functions for each component of psi_T
            self.Gi[ix,0,:,:] = (
                    (self.phi[:,:nup].dot(self.inv_ovlp[0][ix]).dot(t[:,:nup].conj().T)).T
            )
            self.Gi[ix,1,:,:] = (
                    (self.phi[:,nup:].dot(self.inv_ovlp[1][ix]).dot(t[:,nup:].conj().T)).T
            )

    def local_energy(self, system, two_rdm=None):
        """Compute walkers local energy

        Parameters
        ----------
        system : object
            System object.

        Returns
        -------
        (E, T, V) : tuple
            Mixed estimates for walker's energy components.
        """
        return local_energy_multi_det(system, self.Gi,
                                      self.weights, two_rdm=None)

    def contract_one_body(self, ints, trial):
        numer = 0.0
        denom = 0.0
        for i, Gi in enumerate(self.Gi):
            ofac = trial.coeffs[i].conj()*self.ovlps[i]
            numer += ofac * numpy.dot((Gi[0]+Gi[1]).ravel(),ints.ravel())
            denom += ofac
        return numer / denom

    def get_buffer(self):
        """Get walker buffer for MPI communication

        Returns
        -------
        buff : dict
            Relevant walker information for population control.
        """
        s = 0
        buff = numpy.zeros(self.buff_size, dtype=numpy.complex128)
        for d in self.buff_names:
            data = self.__dict__[d]
            if isinstance(data, (numpy.ndarray)):
                buff[s:s+data.size] = data.ravel()
                s += data.size
            else:
                buff[s:s+1] = data
                s += 1
        if self.field_configs is not None:
            stack_buff = self.field_configs.get_buffer()
            return numpy.concatenate((buff,stack_buff))
        else:
            return buff

    def set_buffer(self, buff):
        """Set walker buffer following MPI communication

        Parameters
        -------
        buff : dict
            Relevant walker information for population control.
        """
        s = 0
        for d in self.buff_names:
            data = self.__dict__[d]
            if isinstance(data, numpy.ndarray):
                self.__dict__[d] = buff[s:s+data.size].reshape(data.shape).copy()
                dsize = data.size
            else:
                self.__dict__[d] = buff[s]
                dsize = 1
            s += dsize
        if self.field_configs is not None:
            self.field_configs.set_buffer(buff[self.buff_size:])
