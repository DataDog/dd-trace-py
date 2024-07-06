"""
CAVEAT: the line number is important to some IAST tests, be careful to modify this file and update the tests if you
make some changes
"""
import os


def parametrized_weak_hash(hash_func, method):
    import hashlib

    m = getattr(hashlib, hash_func)()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    # label parametrized_weak_hash
    getattr(m, method)()


def hashlib_new():
    import hashlib

    m = hashlib.new("md5")
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    # label hashlib_new
    m.digest()


def cipher_des(mode):
    from Crypto.Cipher import DES

    key = b"12345678"
    data = b"abcdefgh"
    des_cipher = DES.new(key, mode)
    # label cipher_des
    result = des_cipher.encrypt(data)
    return result


def cipher_blowfish(mode):
    from Crypto.Cipher import Blowfish

    password = b"12345678"
    data = b"abcdefgh"
    crypt_obj = Blowfish.new(password, mode)
    # label cipher_blowfish
    result = crypt_obj.encrypt(data)
    return result


def cipher_arc2(mode):
    from Crypto.Cipher import ARC2

    password = b"12345678"
    data = b"abcdefgh"
    crypt_obj = ARC2.new(password, mode)
    # label cipher_arc2
    result = crypt_obj.encrypt(data)
    return result


def cipher_arc4():
    from Crypto.Cipher import ARC4

    password = b"12345678"
    data = b"abcdefgh"
    crypt_obj = ARC4.new(password)
    # label cipher_arc4
    result = crypt_obj.encrypt(data)
    return result


def cryptography_algorithm(algorithm):
    from cryptography.hazmat.primitives.ciphers import Cipher
    from cryptography.hazmat.primitives.ciphers import algorithms
    from cryptography.hazmat.primitives.ciphers import modes

    data = b"abcdefgh"

    key = b"1234567812345678"
    iv = os.urandom(8)
    mode = modes.CBC(iv)
    if algorithm == "ARC4":
        mode = None

    algorithm = getattr(algorithms, algorithm)(key)
    cipher = Cipher(algorithm, mode=mode)
    # label cryptography_algorithm
    encryptor = cipher.encryptor()
    ct = encryptor.update(data)

    return ct


def cipher_secure():
    from Crypto.Cipher import AES

    key = b"Sixteen byte key"
    data = b"abcdefgh"
    crypt_obj = AES.new(key, AES.MODE_EAX)
    result = crypt_obj.encrypt(data)
    return result
