import numpy
try:
    from pyscf import lib
    einsum = lib.einsum
except ImportError:
    einsum = numpy.einsum

def ekt_1p_fock_opt(h1, cholvec, rdm1a, rdm1b):

    nmo = rdm1a.shape[0]
    assert (len(cholvec.shape) == 2)
    assert (cholvec.shape[0] == nmo*nmo)
    nchol = cholvec.shape[1]
    cholvec = numpy.array(cholvec.todense()).T.reshape((nchol,nmo,nmo))                 

    I = numpy.eye(nmo)
    gamma = I - rdm1a.T + I - rdm1b.T
    rdm1 = rdm1a + rdm1b

    Xa = cholvec.reshape((nchol, nmo*nmo)).dot(rdm1a.ravel())
    Xb = cholvec.reshape((nchol, nmo*nmo)).dot(rdm1b.ravel())

    Xachol = numpy.tensordot(Xa, cholvec, axes=([0],[0]))
    Xbchol = numpy.tensordot(Xb, cholvec, axes=([0],[0]))

    J = 2.0 * (Xachol + Xbchol) - 2.0 * rdm1a.T.dot(Xbchol) - rdm1a.T.dot(Xachol)\
    - rdm1b.T.dot(Xbchol)

    K = numpy.zeros_like(J)

    for x in range(nchol):
        c = cholvec[x,:,:]
        K += - c.dot(rdm1.T).dot(c)
        K += rdm1a.T.dot(c).dot(rdm1a.T).dot(c)
        K += rdm1b.T.dot(c).dot(rdm1b.T).dot(c)

    Fock = gamma.dot(h1) + J + K

    cholvec = cholvec.T.reshape((nmo*nmo,nchol))

    return Fock

def ekt_1h_fock_opt(h1, cholvec, rdm1a, rdm1b):
    
    nmo = rdm1a.shape[0]
    assert (len(cholvec.shape) == 2)
    assert (cholvec.shape[0] == nmo*nmo)
    nchol = cholvec.shape[1]
    cholvec = numpy.array(cholvec.todense()).T.reshape((nchol,nmo,nmo))                 

    Xa = cholvec.reshape((nchol, nmo*nmo)).dot(rdm1a.ravel())
    Xb = cholvec.reshape((nchol, nmo*nmo)).dot(rdm1b.ravel())

    Xachol = numpy.tensordot(Xa, cholvec, axes=([0],[0]))
    Xbchol = numpy.tensordot(Xb, cholvec, axes=([0],[0]))

    J = - 2.0 * rdm1a.dot(Xbchol.T) - rdm1a.dot(Xachol.T) - rdm1b.dot(Xbchol.T)

    K = numpy.zeros_like(J)

    for x in range(nchol):
        c = cholvec[x,:,:]
        K += rdm1a.dot(c.T).dot(rdm1a).dot(c.T)
        K += rdm1a.dot(c.T).dot(rdm1b).dot(c.T)

    gamma = rdm1a+rdm1b
    Fock = - gamma.dot(h1.T) + J + K

    return Fock