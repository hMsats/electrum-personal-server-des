#!/usr/bin/python
from .py2specials import *
from .py3specials import *
import binascii
import hashlib
import re
import sys
import os
import base64
import time
import random
import hmac

is_python2 = sys.version_info.major == 2

# Elliptic curve parameters (secp256k1)

P = 2**256 - 2**32 - 977
N = 115792089237316195423570985008687907852837564279074904382605163141518161494337
A = 0
B = 7
Gx = 55066263022277343669578718895168534326250603453777594175500187360389116729240
Gy = 32670510020758816978083085130507043184471273380659243275938904335757337482424
G = (Gx, Gy)

# Extended Euclidean Algorithm
def inv(a, n):
    lm, hm = 1, 0
    low, high = a % n, n
    while low > 1:
        r = high // low
        nm, new = hm - lm * r, high - low * r
        lm, low, hm, high = nm, new, lm, low
    return lm % n

# Elliptic curve Jordan form functions
# P = (m, n, p, q) where m/n = x, p/q = y

def isinf(p):
    return p[0] == 0 and p[1] == 0


def jordan_isinf(p):
    return p[0][0] == 0 and p[1][0] == 0


def mulcoords(c1, c2):
    return (c1[0] * c2[0] % P, c1[1] * c2[1] % P)


def mul_by_const(c, v):
    return (c[0] * v % P, c[1])


def addcoords(c1, c2):
    return ((c1[0] * c2[1] + c2[0] * c1[1]) % P, c1[1] * c2[1] % P)


def subcoords(c1, c2):
    return ((c1[0] * c2[1] - c2[0] * c1[1]) % P, c1[1] * c2[1] % P)


def invcoords(c):
    return (c[1], c[0])


def jordan_add(a, b):
    if jordan_isinf(a):
        return b
    if jordan_isinf(b):
        return a

    if (a[0][0] * b[0][1] - b[0][0] * a[0][1]) % P == 0:
        if (a[1][0] * b[1][1] - b[1][0] * a[1][1]) % P == 0:
            return jordan_double(a)
        else:
            return ((0, 1), (0, 1))
    xdiff = subcoords(b[0], a[0])
    ydiff = subcoords(b[1], a[1])
    m = mulcoords(ydiff, invcoords(xdiff))
    x = subcoords(subcoords(mulcoords(m, m), a[0]), b[0])
    y = subcoords(mulcoords(m, subcoords(a[0], x)), a[1])
    return (x, y)


def jordan_double(a):
    if jordan_isinf(a):
        return ((0, 1), (0, 1))
    num = addcoords(mul_by_const(mulcoords(a[0], a[0]), 3), (A, 1))
    den = mul_by_const(a[1], 2)
    m = mulcoords(num, invcoords(den))
    x = subcoords(mulcoords(m, m), mul_by_const(a[0], 2))
    y = subcoords(mulcoords(m, subcoords(a[0], x)), a[1])
    return (x, y)


def jordan_multiply(a, n):
    if jordan_isinf(a) or n == 0:
        return ((0, 0), (0, 0))
    if n == 1:
        return a
    if n < 0 or n >= N:
        return jordan_multiply(a, n % N)
    if (n % 2) == 0:
        return jordan_double(jordan_multiply(a, n // 2))
    if (n % 2) == 1:
        return jordan_add(jordan_double(jordan_multiply(a, n // 2)), a)


def to_jordan(p):
    return ((p[0], 1), (p[1], 1))


def from_jordan(p):
    return (p[0][0] * inv(p[0][1], P) % P, p[1][0] * inv(p[1][1], P) % P)

def fast_multiply(a, n):
    return from_jordan(jordan_multiply(to_jordan(a), n))


def fast_add(a, b):
    return from_jordan(jordan_add(to_jordan(a), to_jordan(b)))

# Functions for handling pubkey and privkey formats


def get_pubkey_format(pub):
    if is_python2:
        two = '\x02'
        three = '\x03'
        four = '\x04'
    else:
        two = 2
        three = 3
        four = 4

    if isinstance(pub, (tuple, list)): return 'decimal'
    elif len(pub) == 65 and pub[0] == four: return 'bin'
    elif len(pub) == 130 and pub[0:2] == '04': return 'hex'
    elif len(pub) == 33 and pub[0] in [two, three]: return 'bin_compressed'
    elif len(pub) == 66 and pub[0:2] in ['02', '03']: return 'hex_compressed'
    elif len(pub) == 64: return 'bin_electrum'
    elif len(pub) == 128: return 'hex_electrum'
    else: raise Exception("Pubkey not in recognized format")


def encode_pubkey(pub, formt):
    if not isinstance(pub, (tuple, list)):
        pub = decode_pubkey(pub)
    if formt == 'decimal': return pub
    elif formt == 'bin':
        return b'\x04' + encode(pub[0], 256, 32) + encode(pub[1], 256, 32)
    elif formt == 'bin_compressed':
        return from_int_to_byte(2 + (pub[1] % 2)) + encode(pub[0], 256, 32)
    elif formt == 'hex':
        return '04' + encode(pub[0], 16, 64) + encode(pub[1], 16, 64)
    elif formt == 'hex_compressed':
        return '0' + str(2 + (pub[1] % 2)) + encode(pub[0], 16, 64)
    elif formt == 'bin_electrum':
        return encode(pub[0], 256, 32) + encode(pub[1], 256, 32)
    elif formt == 'hex_electrum':
        return encode(pub[0], 16, 64) + encode(pub[1], 16, 64)
    else:
        raise Exception("Invalid format!")


def decode_pubkey(pub, formt=None):
    if not formt: formt = get_pubkey_format(pub)
    if formt == 'decimal': return pub
    elif formt == 'bin':
        return (decode(pub[1:33], 256), decode(pub[33:65], 256))
    elif formt == 'bin_compressed':
        x = decode(pub[1:33], 256)
        beta = pow(int(x * x * x + A * x + B), int((P + 1) // 4), int(P))
        y = (P - beta) if ((beta + from_byte_to_int(pub[0])) % 2) else beta
        return (x, y)
    elif formt == 'hex':
        return (decode(pub[2:66], 16), decode(pub[66:130], 16))
    elif formt == 'hex_compressed':
        return decode_pubkey(safe_from_hex(pub), 'bin_compressed')
    elif formt == 'bin_electrum':
        return (decode(pub[:32], 256), decode(pub[32:64], 256))
    elif formt == 'hex_electrum':
        return (decode(pub[:64], 16), decode(pub[64:128], 16))
    else:
        raise Exception("Invalid format!")


def get_privkey_format(priv):
    if isinstance(priv, int_types): return 'decimal'
    elif len(priv) == 32: return 'bin'
    elif len(priv) == 33: return 'bin_compressed'
    elif len(priv) == 64: return 'hex'
    elif len(priv) == 66: return 'hex_compressed'
    else:
        bin_p = b58check_to_bin(priv)
        if len(bin_p) == 32: return 'wif'
        elif len(bin_p) == 33: return 'wif_compressed'
        else: raise Exception("WIF does not represent privkey")


def encode_privkey(priv, formt, vbyte=0):
    if not isinstance(priv, int_types):
        return encode_privkey(decode_privkey(priv), formt, vbyte)
    if formt == 'decimal': return priv
    elif formt == 'bin': return encode(priv, 256, 32)
    elif formt == 'bin_compressed': return encode(priv, 256, 32) + b'\x01'
    elif formt == 'hex': return encode(priv, 16, 64)
    elif formt == 'hex_compressed': return encode(priv, 16, 64) + '01'
    elif formt == 'wif':
        return bin_to_b58check(encode(priv, 256, 32), 128 + int(vbyte))
    elif formt == 'wif_compressed':
        return bin_to_b58check(
            encode(priv, 256, 32) + b'\x01', 128 + int(vbyte))
    else:
        raise Exception("Invalid format!")


def decode_privkey(priv, formt=None):
    if not formt: formt = get_privkey_format(priv)
    if formt == 'decimal': return priv
    elif formt == 'bin': return decode(priv, 256)
    elif formt == 'bin_compressed': return decode(priv[:32], 256)
    elif formt == 'hex': return decode(priv, 16)
    elif formt == 'hex_compressed': return decode(priv[:64], 16)
    elif formt == 'wif': return decode(b58check_to_bin(priv), 256)
    elif formt == 'wif_compressed':
        return decode(b58check_to_bin(priv)[:32], 256)
    else:
        raise Exception("WIF does not represent privkey")


def add_pubkeys(p1, p2):
    f1, f2 = get_pubkey_format(p1), get_pubkey_format(p2)
    return encode_pubkey(
        fast_add(
            decode_pubkey(p1, f1), decode_pubkey(p2, f2)), f1)


def add_privkeys(p1, p2):
    f1, f2 = get_privkey_format(p1), get_privkey_format(p2)
    return encode_privkey(
        (decode_privkey(p1, f1) + decode_privkey(p2, f2)) % N, f1)


def multiply(pubkey, privkey):
    f1, f2 = get_pubkey_format(pubkey), get_privkey_format(privkey)
    pubkey, privkey = decode_pubkey(pubkey, f1), decode_privkey(privkey, f2)
    # http://safecurves.cr.yp.to/twist.html
    if not isinf(pubkey) and (
            pubkey[0]**3 + B - pubkey[1] * pubkey[1]) % P != 0:
        raise Exception("Point not on curve")
    return encode_pubkey(fast_multiply(pubkey, privkey), f1)


def divide(pubkey, privkey):
    factor = inv(decode_privkey(privkey), N)
    return multiply(pubkey, factor)


def compress(pubkey):
    f = get_pubkey_format(pubkey)
    if 'compressed' in f: return pubkey
    elif f == 'bin':
        return encode_pubkey(decode_pubkey(pubkey, f), 'bin_compressed')
    elif f == 'hex' or f == 'decimal':
        return encode_pubkey(decode_pubkey(pubkey, f), 'hex_compressed')


def decompress(pubkey):
    f = get_pubkey_format(pubkey)
    if 'compressed' not in f: return pubkey
    elif f == 'bin_compressed':
        return encode_pubkey(decode_pubkey(pubkey, f), 'bin')
    elif f == 'hex_compressed' or f == 'decimal':
        return encode_pubkey(decode_pubkey(pubkey, f), 'hex')


def privkey_to_pubkey(privkey):
    f = get_privkey_format(privkey)
    privkey = decode_privkey(privkey, f)
    if privkey >= N:
        raise Exception("Invalid privkey")
    if f in ['bin', 'bin_compressed', 'hex', 'hex_compressed', 'decimal']:
        return encode_pubkey(fast_multiply(G, privkey), f)
    else:
        return encode_pubkey(fast_multiply(G, privkey), f.replace('wif', 'hex'))


privtopub = privkey_to_pubkey


def privkey_to_address(priv, magicbyte=0):
    return pubkey_to_address(privkey_to_pubkey(priv), magicbyte)


privtoaddr = privkey_to_address


def neg_pubkey(pubkey):
    f = get_pubkey_format(pubkey)
    pubkey = decode_pubkey(pubkey, f)
    return encode_pubkey((pubkey[0], (P - pubkey[1]) % P), f)


def neg_privkey(privkey):
    f = get_privkey_format(privkey)
    privkey = decode_privkey(privkey, f)
    return encode_privkey((N - privkey) % N, f)


def subtract_pubkeys(p1, p2):
    f1, f2 = get_pubkey_format(p1), get_pubkey_format(p2)
    k2 = decode_pubkey(p2, f2)
    return encode_pubkey(
        fast_add(
            decode_pubkey(p1, f1), (k2[0], (P - k2[1]) % P)), f1)


def subtract_privkeys(p1, p2):
    f1, f2 = get_privkey_format(p1), get_privkey_format(p2)
    k2 = decode_privkey(p2, f2)
    return encode_privkey((decode_privkey(p1, f1) - k2) % N, f1)

# Hashes


def bin_hash160(string):
    intermed = hashlib.sha256(string).digest()
    digest = ''
    digest = hashlib.new('ripemd160', intermed).digest()
    return digest


def hash160(string):
    return safe_hexlify(bin_hash160(string))


def bin_sha256(string):
    binary_data = string if isinstance(string, bytes) else bytes(string,
                                                                 'utf-8')
    return hashlib.sha256(binary_data).digest()


def sha256(string):
    return bytes_to_hex_string(bin_sha256(string))


def bin_ripemd160(string):
    digest = hashlib.new('ripemd160', string).digest()
    return digest


def ripemd160(string):
    return safe_hexlify(bin_ripemd160(string))


def bin_dbl_sha256(s):
    bytes_to_hash = from_string_to_bytes(s)
    return hashlib.sha256(hashlib.sha256(bytes_to_hash).digest()).digest()


def dbl_sha256(string):
    return safe_hexlify(bin_dbl_sha256(string))


def bin_slowsha(string):
    string = from_string_to_bytes(string)
    orig_input = string
    for i in range(100000):
        string = hashlib.sha256(string + orig_input).digest()
    return string


def slowsha(string):
    return safe_hexlify(bin_slowsha(string))


def hash_to_int(x):
    if len(x) in [40, 64]:
        return decode(x, 16)
    return decode(x, 256)


def num_to_var_int(x):
    x = int(x)
    if x < 253: return from_int_to_byte(x)
    elif x < 65536: return from_int_to_byte(253) + encode(x, 256, 2)[::-1]
    elif x < 4294967296: return from_int_to_byte(254) + encode(x, 256, 4)[::-1]
    else: return from_int_to_byte(255) + encode(x, 256, 8)[::-1]


# WTF, Electrum?
def electrum_sig_hash(message):
    padded = b"\x18Bitcoin Signed Message:\n" + num_to_var_int(len(
        message)) + from_string_to_bytes(message)
    return bin_dbl_sha256(padded)

# Encodings

def b58check_to_bin(inp):
    leadingzbytes = len(re.match('^1*', inp).group(0))
    data = b'\x00' * leadingzbytes + changebase(inp, 58, 256)
    assert bin_dbl_sha256(data[:-4])[:4] == data[-4:]
    return data[1:-4]

def b58check_to_bin_full(inp):
    leadingzbytes = len(re.match('^1*', inp).group(0))
    data = b'\x00' * leadingzbytes + changebase(inp, 58, 256)
    assert bin_dbl_sha256(data[:-4])[:4] == data[-4:]
    return data[:-4]


def get_version_byte(inp):
    leadingzbytes = len(re.match('^1*', inp).group(0))
    data = b'\x00' * leadingzbytes + changebase(inp, 58, 256)
    assert bin_dbl_sha256(data[:-4])[:4] == data[-4:]
    return ord(data[0])


def hex_to_b58check(inp, magicbyte=0):
    return bin_to_b58check(binascii.unhexlify(inp), magicbyte)


def b58check_to_hex(inp):
    return safe_hexlify(b58check_to_bin(inp))


def pubkey_to_address(pubkey, magicbyte=0):
    if isinstance(pubkey, (list, tuple)):
        pubkey = encode_pubkey(pubkey, 'bin')
    if len(pubkey) in [66, 130]:
        return bin_to_b58check(
            bin_hash160(binascii.unhexlify(pubkey)), magicbyte)
    return bin_to_b58check(bin_hash160(pubkey), magicbyte)


pubtoaddr = pubkey_to_address

# EDCSA


def encode_sig(v, r, s):
    vb, rb, sb = from_int_to_byte(v), encode(r, 256), encode(s, 256)

    result = base64.b64encode(vb + b'\x00' * (32 - len(rb)) + rb + b'\x00' * (
        32 - len(sb)) + sb)
    return result if is_python2 else str(result, 'utf-8')


def decode_sig(sig):
    bytez = base64.b64decode(sig)
    return from_byte_to_int(bytez[0]), decode(bytez[1:33], 256), decode(
        bytez[33:], 256)

# https://tools.ietf.org/html/rfc6979#section-3.2


def deterministic_generate_k(msghash, priv):
    v = b'\x01' * 32
    k = b'\x00' * 32
    priv = encode_privkey(priv, 'bin')
    msghash = encode(hash_to_int(msghash), 256, 32)
    k = hmac.new(k, v + b'\x00' + priv + msghash, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b'\x01' + priv + msghash, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    return decode(hmac.new(k, v, hashlib.sha256).digest(), 256)


def ecdsa_raw_sign(msghash, priv):

    z = hash_to_int(msghash)
    k = deterministic_generate_k(msghash, priv)

    r, y = fast_multiply(G, k)
    s = inv(k, N) * (z + r * decode_privkey(priv)) % N

    return 27 + (y % 2), r, s


def ecdsa_sign(msg, priv):
    return encode_sig(*ecdsa_raw_sign(electrum_sig_hash(msg), priv))

def ecdsa_raw_verify(msghash, vrs, pub):
    v, r, s = vrs

    w = inv(s, N)
    z = hash_to_int(msghash)

    u1, u2 = z * w % N, r * w % N
    x, y = fast_add(fast_multiply(G, u1), fast_multiply(decode_pubkey(pub), u2))

    return r == x

def ecdsa_verify(msg, sig, pub):
    return ecdsa_raw_verify(electrum_sig_hash(msg), decode_sig(sig), pub)

def estimate_tx_size(ins, outs, txtype='p2pkh'):
    '''Estimate transaction size.
    Assuming p2pkh:
    out: 8+1+3+2+20=34, in: 1+32+4+1+1+~73+1+1+33=147,
    ver:4,seq:4, +2 (len in,out)
    total ~= 34*len_out + 147*len_in + 10 (sig sizes vary slightly)
    '''
    if txtype=='p2pkh':
        return 10 + ins*147 +34*outs
    else:
        raise NotImplementedError("Non p2pkh transaction size estimation not"+
                                  "yet implemented")
