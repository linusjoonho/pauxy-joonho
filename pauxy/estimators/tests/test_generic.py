import numpy
import pytest
from pauxy.systems.generic import Generic
from pauxy.trial_wavefunction.multi_slater import MultiSlater
from pauxy.estimators.generic import (
        local_energy_generic_opt,
        local_energy_generic_cholesky,
        local_energy_generic_cholesky_opt
        )
from pauxy.utils.testing import (
        generate_hamiltonian,
        get_random_nomsd
        )

@pytest.mark.unit
def test_local_energy_opt():
    numpy.random.seed(7)
    nmo = 24
    nelec = (4,2)
    h1e, chol, enuc, eri = generate_hamiltonian(nmo, nelec, cplx=False)
    sys = Generic(nelec=nelec, h1e=h1e, chol=chol, ecore=enuc,
                  inputs={'integral_tensor': True})
    wfn = get_random_nomsd(sys, ndet=1, cplx=False)
    trial = MultiSlater(sys, wfn)
    sys.construct_integral_tensors_real(trial)
    e = local_energy_generic_opt(sys, trial.G, Ghalf=trial.GH)
    assert e[0] == pytest.approx(20.6826247016273)
    assert e[1] == pytest.approx(23.0173528796140)
    assert e[2] == pytest.approx(-2.3347281779866)

@pytest.mark.unit
def test_local_energy_cholesky():
    numpy.random.seed(7)
    nmo = 24
    nelec = (4,2)
    h1e, chol, enuc, eri = generate_hamiltonian(nmo, nelec, cplx=False)
    sys = Generic(nelec=nelec, h1e=h1e, chol=chol, ecore=enuc)
    wfn = get_random_nomsd(sys, ndet=1, cplx=False)
    trial = MultiSlater(sys, wfn)
    sys.construct_integral_tensors_real(trial)
    e = local_energy_generic_cholesky(sys, trial.G, Ghalf=trial.GH)
    assert e[0] == pytest.approx(20.6826247016273)
    assert e[1] == pytest.approx(23.0173528796140)
    assert e[2] == pytest.approx(-2.3347281779866)

@pytest.mark.unit
def test_local_energy_cholesky_opt():
    numpy.random.seed(7)
    nmo = 24
    nelec = (4,2)
    h1e, chol, enuc, eri = generate_hamiltonian(nmo, nelec, cplx=False)
    sys = Generic(nelec=nelec, h1e=h1e, chol=chol, ecore=enuc)
    wfn = get_random_nomsd(sys, ndet=1, cplx=False)
    trial = MultiSlater(sys, wfn)
    sys.construct_integral_tensors_real(trial)
    e = local_energy_generic_cholesky_opt(sys, trial.G, Ghalf=trial.GH)
    assert e[0] == pytest.approx(20.6826247016273)
    assert e[1] == pytest.approx(23.0173528796140)
    assert e[2] == pytest.approx(-2.3347281779866)
