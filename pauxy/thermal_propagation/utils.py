"""Routines for performing propagation of a walker"""

from pauxy.thermal_propagation.continuous import Continuous
from pauxy.thermal_propagation.planewave import PlaneWave
from pauxy.thermal_propagation.hubbard import ThermalDiscrete


def get_propagator(options, qmc, system, trial, verbose=False):
    """Wrapper to select propagator class.

    Parameters
    ----------
    options : dict
        Propagator input options.
    qmc : :class:`pauxy.qmc.QMCOpts` class
        Trial wavefunction input options.
    system : class
        System class.
    trial : class
        Trial wavefunction object.

    Returns
    -------
    propagator : class or None
        Propagator object.
    """
    if system.name == "Hubbard":
        hs_type = options.get('hubbard_stratonovich', 'discrete')
        if hs_type == "discrete":
            propagator = ThermalDiscrete(options, qmc, system, trial, verbose)
        else:
            propagator = Continuous(options, qmc, system, trial, verbose)
    else:
        if system.name == "UEG":
            propagator = PlaneWave(system, trial, qmc,
                                   options=options, verbose=verbose)
        else:
            propagator = Continuous(options, qmc, system, trial, verbose)

    return propagator
