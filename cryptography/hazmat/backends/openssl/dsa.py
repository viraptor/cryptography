# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import, division, print_function

from cryptography import utils
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.backends.openssl.utils import _truncate_digest
from cryptography.hazmat.primitives import hashes, interfaces
from cryptography.hazmat.primitives.asymmetric import dsa
from cryptography.hazmat.primitives.interfaces import (
    DSAParametersWithNumbers, DSAPrivateKeyWithNumbers, DSAPublicKeyWithNumbers
)


def _truncate_digest_for_dsa(dsa_cdata, digest, backend):
    """
    This function truncates digests that are longer than a given DS
    key's length so they can be signed. OpenSSL does this for us in
    1.0.0c+ and it isn't needed in 0.9.8, but that leaves us with three
    releases (1.0.0, 1.0.0a, and 1.0.0b) where this is a problem. This
    truncation is not required in 0.9.8 because DSA is limited to SHA-1.
    """

    order_bits = backend._lib.BN_num_bits(dsa_cdata.q)
    return _truncate_digest(digest, order_bits)


@utils.register_interface(interfaces.AsymmetricVerificationContext)
class _DSAVerificationContext(object):
    def __init__(self, backend, public_key, signature, algorithm):
        self._backend = backend
        self._public_key = public_key
        self._signature = signature
        self._algorithm = algorithm

        self._hash_ctx = hashes.Hash(self._algorithm, self._backend)

    def update(self, data):
        self._hash_ctx.update(data)

    def verify(self):
        self._dsa_cdata = self._backend._ffi.gc(self._public_key._dsa_cdata,
                                                self._backend._lib.DSA_free)

        data_to_verify = self._hash_ctx.finalize()

        data_to_verify = _truncate_digest_for_dsa(
            self._dsa_cdata, data_to_verify, self._backend
        )

        # The first parameter passed to DSA_verify is unused by OpenSSL but
        # must be an integer.
        res = self._backend._lib.DSA_verify(
            0, data_to_verify, len(data_to_verify), self._signature,
            len(self._signature), self._public_key._dsa_cdata)

        if res != 1:
            errors = self._backend._consume_errors()
            assert errors
            if res == -1:
                assert errors[0].lib == self._backend._lib.ERR_LIB_ASN1

            raise InvalidSignature


@utils.register_interface(interfaces.AsymmetricSignatureContext)
class _DSASignatureContext(object):
    def __init__(self, backend, private_key, algorithm):
        self._backend = backend
        self._private_key = private_key
        self._algorithm = algorithm
        self._hash_ctx = hashes.Hash(self._algorithm, self._backend)

    def update(self, data):
        self._hash_ctx.update(data)

    def finalize(self):
        data_to_sign = self._hash_ctx.finalize()
        data_to_sign = _truncate_digest_for_dsa(
            self._private_key._dsa_cdata, data_to_sign, self._backend
        )
        sig_buf_len = self._backend._lib.DSA_size(self._private_key._dsa_cdata)
        sig_buf = self._backend._ffi.new("unsigned char[]", sig_buf_len)
        buflen = self._backend._ffi.new("unsigned int *")

        # The first parameter passed to DSA_sign is unused by OpenSSL but
        # must be an integer.
        res = self._backend._lib.DSA_sign(
            0, data_to_sign, len(data_to_sign), sig_buf,
            buflen, self._private_key._dsa_cdata)
        assert res == 1
        assert buflen[0]

        return self._backend._ffi.buffer(sig_buf)[:buflen[0]]


@utils.register_interface(DSAParametersWithNumbers)
class _DSAParameters(object):
    def __init__(self, backend, dsa_cdata):
        self._backend = backend
        self._dsa_cdata = dsa_cdata

    def parameter_numbers(self):
        return dsa.DSAParameterNumbers(
            p=self._backend._bn_to_int(self._dsa_cdata.p),
            q=self._backend._bn_to_int(self._dsa_cdata.q),
            g=self._backend._bn_to_int(self._dsa_cdata.g)
        )

    def generate_private_key(self):
        return self._backend.generate_dsa_private_key(self)


@utils.register_interface(DSAPrivateKeyWithNumbers)
class _DSAPrivateKey(object):
    def __init__(self, backend, dsa_cdata):
        self._backend = backend
        self._dsa_cdata = dsa_cdata
        self._key_size = self._backend._lib.BN_num_bits(self._dsa_cdata.p)

    key_size = utils.read_only_property("_key_size")

    def signer(self, signature_algorithm):
        return _DSASignatureContext(self._backend, self, signature_algorithm)

    def private_numbers(self):
        return dsa.DSAPrivateNumbers(
            public_numbers=dsa.DSAPublicNumbers(
                parameter_numbers=dsa.DSAParameterNumbers(
                    p=self._backend._bn_to_int(self._dsa_cdata.p),
                    q=self._backend._bn_to_int(self._dsa_cdata.q),
                    g=self._backend._bn_to_int(self._dsa_cdata.g)
                ),
                y=self._backend._bn_to_int(self._dsa_cdata.pub_key)
            ),
            x=self._backend._bn_to_int(self._dsa_cdata.priv_key)
        )

    def public_key(self):
        dsa_cdata = self._backend._lib.DSA_new()
        assert dsa_cdata != self._backend._ffi.NULL
        dsa_cdata = self._backend._ffi.gc(
            dsa_cdata, self._backend._lib.DSA_free
        )
        dsa_cdata.p = self._backend._lib.BN_dup(self._dsa_cdata.p)
        dsa_cdata.q = self._backend._lib.BN_dup(self._dsa_cdata.q)
        dsa_cdata.g = self._backend._lib.BN_dup(self._dsa_cdata.g)
        dsa_cdata.pub_key = self._backend._lib.BN_dup(self._dsa_cdata.pub_key)
        return _DSAPublicKey(self._backend, dsa_cdata)

    def parameters(self):
        dsa_cdata = self._backend._lib.DSA_new()
        assert dsa_cdata != self._backend._ffi.NULL
        dsa_cdata = self._backend._ffi.gc(
            dsa_cdata, self._backend._lib.DSA_free
        )
        dsa_cdata.p = self._backend._lib.BN_dup(self._dsa_cdata.p)
        dsa_cdata.q = self._backend._lib.BN_dup(self._dsa_cdata.q)
        dsa_cdata.g = self._backend._lib.BN_dup(self._dsa_cdata.g)
        return _DSAParameters(self._backend, dsa_cdata)


@utils.register_interface(DSAPublicKeyWithNumbers)
class _DSAPublicKey(object):
    def __init__(self, backend, dsa_cdata):
        self._backend = backend
        self._dsa_cdata = dsa_cdata
        self._key_size = self._backend._lib.BN_num_bits(self._dsa_cdata.p)

    key_size = utils.read_only_property("_key_size")

    def verifier(self, signature, signature_algorithm):
        return _DSAVerificationContext(
            self._backend, self, signature, signature_algorithm
        )

    def public_numbers(self):
        return dsa.DSAPublicNumbers(
            parameter_numbers=dsa.DSAParameterNumbers(
                p=self._backend._bn_to_int(self._dsa_cdata.p),
                q=self._backend._bn_to_int(self._dsa_cdata.q),
                g=self._backend._bn_to_int(self._dsa_cdata.g)
            ),
            y=self._backend._bn_to_int(self._dsa_cdata.pub_key)
        )

    def parameters(self):
        dsa_cdata = self._backend._lib.DSA_new()
        assert dsa_cdata != self._backend._ffi.NULL
        dsa_cdata = self._backend._ffi.gc(
            dsa_cdata, self._backend._lib.DSA_free
        )
        dsa_cdata.p = self._backend._lib.BN_dup(self._dsa_cdata.p)
        dsa_cdata.q = self._backend._lib.BN_dup(self._dsa_cdata.q)
        dsa_cdata.g = self._backend._lib.BN_dup(self._dsa_cdata.g)
        return _DSAParameters(self._backend, dsa_cdata)
