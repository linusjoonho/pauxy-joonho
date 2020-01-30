import copy
import cmath
import h5py
import math
import numpy
import scipy.linalg
import time
from pauxy.walkers.multi_ghf import MultiGHFWalker
from pauxy.walkers.single_det import SingleDetWalker
from pauxy.walkers.multi_det import MultiDetWalker
from pauxy.walkers.thermal import ThermalWalker
from pauxy.walkers.stack import FieldConfig
from pauxy.qmc.comm import FakeComm
from pauxy.utils.io import get_input_value
from pauxy.utils.misc import update_stack


class Walkers(object):
    """Container for groups of walkers which make up a wavefunction.

    Parameters
    ----------
    system : object
        System object.
    trial : object
        Trial wavefunction object.
    nwalkers : int
        Number of walkers to initialise.
    nprop_tot : int
        Total number of propagators to store for back propagation + itcf.
    nbp : int
        Number of back propagation steps.
    """

    def __init__(self, walker_opts, system, trial, qmc, verbose=False,
                 comm=None, nprop_tot=None, nbp=None):
        self.nwalkers = qmc.nwalkers
        self.ntot_walkers = qmc.ntot_walkers
        self.write_freq = walker_opts.get('write_freq', 0)
        self.write_file = walker_opts.get('write_file', 'restart.h5')
        self.read_file = walker_opts.get('read_file', None)
        if comm is None:
            rank = 0
        else:
            rank = comm.rank
        if verbose:
            print("# Setting up wavefunction object.")
        if trial.name == 'MultiSlater':
            self.walker_type = 'MSD'
            # TODO: FDM FIXTHIS
            if trial.ndets == 1:
                if verbose:
                    print("# Usinge single det walker with msd wavefunction.")
                self.walker_type = 'SD'
                self.walkers = [SingleDetWalker(walker_opts, system, trial,
                                                index=w, nprop_tot=nprop_tot,
                                                nbp=nbp)
                                for w in range(qmc.nwalkers)]
            else:
                self.walkers = [
                        MultiDetWalker(walker_opts, system, trial,
                                       verbose=(verbose and w == 0))
                        for w in range(qmc.nwalkers)
                        ]
            self.buff_size = self.walkers[0].buff_size
            if nbp is not None:
                self.buff_size += self.walkers[0].field_configs.buff_size
            self.walker_buffer = numpy.zeros(self.buff_size,
                                             dtype=numpy.complex128)
        elif trial.name == 'thermal':
            self.walker_type = 'thermal'
            self.walkers = [ThermalWalker(walker_opts, system, trial, verbose and w==0)
                            for w in range(qmc.nwalkers)]
            self.buff_size = self.walkers[0].buff_size + self.walkers[0].stack.buff_size
            self.walker_buffer = numpy.zeros(self.buff_size,
                                             dtype=numpy.complex128)
            stack_size = self.walkers[0].stack_size
            if system.name == "Hubbard":
                if stack_size % qmc.nstblz != 0 or qmc.nstblz < stack_size:
                    if verbose:
                        print("# Stabilisation frequency is not commensurate "
                              "with stack size.")
                        print("# Determining a better value.")
                    if qmc.nstblz < stack_size:
                        qmc.nstblz = stack_size
                        if verbose:
                            print("# Updated stabilization frequency: "
                                  " {}".format(qmc.nstblz))
                    else:
                        qmc.nstblz = update_stack(qmc.nstblz, stack_size,
                                                  name="nstblz", verbose=verbose)
        else:
            self.walker_type = 'SD'
            self.walkers = [SingleDetWalker(walker_opts, system, trial,
                                            index=w, nprop_tot=nprop_tot,
                                            nbp=nbp)
                            for w in range(qmc.nwalkers)]
            self.buff_size = self.walkers[0].buff_size
            if nbp is not None:
                if verbose:
                    print("# Performing back propagation.")
                    print("# Number of steps in imaginary time: {:}.".format(nb))
                self.buff_size += self.walkers[0].field_configs.buff_size
            self.walker_buffer = numpy.zeros(self.buff_size,
                                             dtype=numpy.complex128)
        if system.name == "Generic" or system.name == "UEG":
            dtype = complex
        else:
            dtype = int

        if (system.name == "HubbardHolstein"):
            self.holstein = True
        else:
            self.holstein = False

        self.pcont_method = get_input_value(walker_opts, 'population_control',
                                            default='comb')
        self.min_weight = walker_opts.get('min_weight', 0.1)
        self.max_weight = walker_opts.get('max_weight', 4.0)
        if verbose:
            print("# Using {} population control "
                  "algorithm.".format(self.pcont_method))
            mem = float(self.walker_buffer.nbytes) / (1024.0**3)
            print("# Buffer size for communication: {:13.8e} GB".format(mem))
            if mem > 2.0:
                # TODO: FDM FIX THIS
                print(" # Warning: Walker buffer size > 2GB. May run into MPI"
                      "issues.")
        if not self.walker_type == "thermal":
            walker_size = 3 + self.walkers[0].phi.size
        if self.write_freq > 0:
            self.write_restart = True
            self.dsets = []
            with h5py.File(self.write_file,'w',driver='mpio',comm=comm) as fh5:
                for i in range(self.ntot_walkers):
                    fh5.create_dataset('walker_%d'%i, (walker_size,),
                                       dtype=numpy.complex128)

        else:
            self.write_restart = False
        if self.read_file is not None:
            if verbose:
                print("# Reading walkers from %s file series."%self.read_file)
            self.read_walkers(comm)
        self.target_weight = qmc.ntot_walkers
        self.nw = qmc.nwalkers
        self.set_total_weight(qmc.ntot_walkers)

    def orthogonalise(self, trial, free_projection):
        """Orthogonalise all walkers.

        Parameters
        ----------
        trial : object
            Trial wavefunction object.
        free_projection : bool
            True if doing free projection.
        """
        for w in self.walkers:
            detR = w.reortho(trial)
            if free_projection:
                (magn, dtheta) = cmath.polar(detR)
                w.weight *= magn
                w.phase *= cmath.exp(1j*dtheta)

    def add_field_config(self, nprop_tot, nbp, system, dtype):
        """Add FieldConfig object to walker object.

        Parameters
        ----------
        nprop_tot : int
            Total number of propagators to store for back propagation + itcf.
        nbp : int
            Number of back propagation steps.
        nfields : int
            Number of fields to store for each back propagation step.
        dtype : type
            Field configuration type.
        """
        for w in self.walkers:
            w.field_configs = FieldConfig(system.nfields, nprop_tot, nbp, dtype)

    def copy_historic_wfn(self):
        """Copy current wavefunction to psi_n for next back propagation step."""
        for (i,w) in enumerate(self.walkers):
            numpy.copyto(self.walkers[i].phi_old, self.walkers[i].phi)

    def copy_bp_wfn(self, phi_bp):
        """Copy back propagated wavefunction.

        Parameters
        ----------
        phi_bp : object
            list of walker objects containing back propagated walkers.
        """
        for (i, (w,wbp)) in enumerate(zip(self.walkers, phi_bp)):
            numpy.copyto(self.walkers[i].phi_bp, wbp.phi)

    def copy_init_wfn(self):
        """Copy current wavefunction to initial wavefunction.

        The definition of the initial wavefunction depends on whether we are
        calculating an ITCF or not.
        """
        for (i,w) in enumerate(self.walkers):
            numpy.copyto(self.walkers[i].phi_right, self.walkers[i].phi)

    def pop_control(self, comm):
        weights = numpy.array([abs(w.weight) for w in self.walkers])
        if comm.rank == 0:
            global_weights = numpy.empty(len(weights)*comm.size)
        else:
            global_weights = numpy.empty(len(weights)*comm.size)
        comm.Allgather(weights, global_weights)
        total_weight = sum(global_weights)
        # Rescale weights to combat exponential decay/growth.
        scale = total_weight / self.target_weight
        if total_weight < 1e-8:
            if comm.rank == 0:
                print("# Warning: Total weight is {:13.8e}: "
                      .format(total_weight))
                print("# Something is seriously wrong.")
        self.set_total_weight(total_weight)
        # Todo: Just standardise information we want to send between routines.
        for w in self.walkers:
            w.unscaled_weight = w.weight
            w.weight = w.weight / scale
        if self.pcont_method == "comb":
            global_weights = global_weights / scale
            self.comb(comm, global_weights)
        elif self.pcont_method == "pair_branch":
            self.pair_branch(comm)
        else:
            if comm.rank == 0:
                print("Unknown population control method.")

    def comb(self, comm, weights):
        """Apply the comb method of population control / branching.

        See Booth & Gubernatis PRE 80, 046704 (2009).

        Parameters
        ----------
        comm : MPI communicator
        """
        # Need make a copy to since the elements in psi are only references to
        # walker objects in memory. We don't want future changes in a given
        # element of psi having unintended consequences.
        # todo : add phase to walker for free projection
        if comm.rank == 0:
            parent_ix = numpy.zeros(len(weights), dtype='i')
        else:
            parent_ix = numpy.empty(len(weights), dtype='i')
        if comm.rank == 0:
            total_weight = sum(weights)
            cprobs = numpy.cumsum(weights)
            r = numpy.random.random()
            comb = [(i+r) * (total_weight/self.target_weight) for i in
                    range(self.target_weight)]
            iw = 0
            ic = 0
            while ic < len(comb):
                if comb[ic] < cprobs[iw]:
                    parent_ix[iw] += 1
                    ic += 1
                else:
                    iw += 1
            data = {'ix': parent_ix}
        else:
            data = None

        data = comm.bcast(data, root=0)
        parent_ix = data['ix']
        # Keep total weight saved for capping purposes.
        # where returns a tuple (array,), selecting first element.
        kill = numpy.where(parent_ix == 0)[0]
        clone = numpy.where(parent_ix > 1)[0]
        reqs = []
        walker_buffers = []
        # First initiate non-blocking sends of walkers.
        for i, (c, k) in enumerate(zip(clone, kill)):
            # Sending from current processor?
            if c // self.nw == comm.rank:
                # Location of walker to clone in local list.
                clone_pos = c % self.nw
                # copying walker data to intermediate buffer to avoid issues
                # with accessing walker data during send. Might not be
                # necessary.
                dest_proc = k // self.nw
                buff = self.walkers[clone_pos].get_buffer()
                reqs.append(comm.Isend(buff, dest=dest_proc, tag=i))
        # Now receive walkers on processors where walkers are to be killed.
        for i, (c, k) in enumerate(zip(clone, kill)):
            # Receiving to current processor?
            if k // self.nw == comm.rank:
                # Processor we are receiving from.
                source_proc = c // self.nw
                # Location of walker to kill in local list of walkers.
                kill_pos = k % self.nw
                comm.Recv(self.walker_buffer, source=source_proc, tag=i)
                self.walkers[kill_pos].set_buffer(self.walker_buffer)
        # Complete non-blocking send.
        for rs in reqs:
            rs.wait()
        # Necessary?
        comm.Barrier()
        # Reset walker weight.
        # TODO: check this.
        for w in self.walkers:
            w.weight = 1.0

    def pair_branch(self, comm):
        walker_info = [[abs(w.weight),1,comm.rank,comm.rank] for w in self.walkers]
        glob_inf = comm.gather(walker_info, root=0)
        # Want same random number seed used on all processors
        if comm.rank == 0:
            # Rescale weights.
            glob_inf = numpy.array([item for sub in glob_inf for item in sub])
            total_weight = sum(w[0] for w in glob_inf)
            sort = numpy.argsort(glob_inf[:,0], kind='mergesort')
            isort = numpy.argsort(sort, kind='mergesort')
            glob_inf = glob_inf[sort]
            s = 0
            e = len(glob_inf) - 1
            tags = []
            isend = 0
            while s < e:
                if glob_inf[s][0] < self.min_weight or glob_inf[e][0] > self.max_weight:
                    # sum of paired walker weights
                    wab = glob_inf[s][0] + glob_inf[e][0]
                    r = numpy.random.rand()
                    if r < glob_inf[e][0] / wab:
                        # clone large weight walker
                        glob_inf[e][0] = 0.5 * wab
                        glob_inf[e][1] = 2
                        # Processor we will send duplicated walker to
                        glob_inf[e][3] = glob_inf[s][2]
                        send = glob_inf[s][2]
                        # Kill small weight walker
                        glob_inf[s][0] = 0.0
                        glob_inf[s][1] = 0
                        glob_inf[s][3] = glob_inf[e][2]
                    else:
                        # clone small weight walker
                        glob_inf[s][0] = 0.5 * wab
                        glob_inf[s][1] = 2
                        # Processor we will send duplicated walker to
                        glob_inf[s][3] = glob_inf[e][2]
                        send = glob_inf[e][2]
                        # Kill small weight walker
                        glob_inf[e][0] = 0.0
                        glob_inf[e][1] = 0
                        glob_inf[e][3] = glob_inf[s][2]
                    tags.append([send])
                    s += 1
                    e -= 1
                else:
                    break
            nw = self.nwalkers
            glob_inf = glob_inf[isort].reshape((comm.size,nw,4))
        else:
            data = None
            total_weight = 0
        data = comm.scatter(glob_inf, root=0)
        # Keep total weight saved for capping purposes.
        walker_buffers = []
        reqs = []
        for iw, walker in enumerate(data):
            if walker[1] > 1:
                tag = comm.rank*len(walker_info) + walker[3]
                self.walkers[iw].weight = walker[0]
                buff = self.walkers[iw].get_buffer()
                reqs.append(comm.Isend(buff,
                                       dest=int(round(walker[3])),
                                       tag=tag))
        for iw, walker in enumerate(data):
            if walker[1] == 0:
                tag = walker[3]*len(walker_info) + comm.rank
                comm.Recv(self.walker_buffer,
                          source=int(round(walker[3])),
                          tag=tag)
                self.walkers[iw].set_buffer(self.walker_buffer)
        for r in reqs:
            r.wait()


    def recompute_greens_function(self, trial, time_slice=None):
        for w in self.walkers:
            w.greens_function(trial, time_slice)

    def set_total_weight(self, total_weight):
        for w in self.walkers:
            w.total_weight = total_weight
            w.old_total_weight = w.total_weight

    def reset(self, trial):
        for w in self.walkers:
            w.stack.reset()
            w.stack.set_all(trial.dmat)
            w.greens_function(trial)
            w.weight = 1.0
            w.phase = 1.0 + 0.0j

    def get_write_buffer(self, i):
        w = self.walkers[i]
        
        if (self.holstein):
            buff = numpy.concatenate([[w.weight], [w.phase], [w.ot], w.phi.ravel(), w.X.ravel()])
        else:
            buff = numpy.concatenate([[w.weight], [w.phase], [w.ot], w.phi.ravel()])

        return buff

    def set_walker_from_buffer(self, i, buff):
        w = self.walkers[i]
        w.weight = buff[0]
        w.phase = buff[1]
        w.ot = buff[2]
        nelem = numpy.prod(self.walkers[i].phi.shape)
        w.phi = buff[3:3+nelem].reshape(self.walkers[i].phi.shape)
        if (self.holstein):
            w.X = buff[3+nelem:]

    def write_walkers(self, comm):
        start = time.time()
        with h5py.File(self.write_file,'r+',driver='mpio',comm=comm) as fh5:
            for (i,w) in enumerate(self.walkers):
                ix = i + self.nwalkers*comm.rank
                # buff = self.get_write_buffer(i)
                fh5['walker_%d'%ix][:] = self.get_write_buffer(i)
        if comm.rank == 0:
            print(" # Writing walkers to file.")
            print(" # Time to write restart: {:13.8e} s"
                  .format(time.time()-start))

    def read_walkers(self, comm):
        with h5py.File(self.read_file, 'r') as fh5:
            for (i,w) in enumerate(self.walkers):
                try:
                    ix = i + self.nwalkers*comm.rank
                    self.set_walker_from_buffer(i, fh5['walker_%d'%ix][:])
                except KeyError:
                    print(" # Could not read walker data from:"
                          " %s"%(self.read_file))
