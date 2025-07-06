#
#  GoogleFindMyTools - A set of tools to interact with the Google Find My API
#  Copyright © 2024 Leon Böttger. All rights reserved.
#

import secrets
from binascii import unhexlify

from Cryptodome.Cipher import AES
from ecdsa import SECP160r1
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from ecdsa.ellipticcurve import Point

from FMDNCrypto.eid_generator import generate_eid, calculate_r



def rx_to_ry(Rx: int, curve) -> int:
    # Calculate y^2 = x^3 + ax + b (mod p)
    Ryy = (Rx ** 3 + curve.a() * Rx + curve.b()) % curve.p()

    # Calculate modular square root
    Ry = pow(Ryy, (curve.p() + 1) // 4, curve.p())

    # Verify the result
    if (Ry ** 2 % curve.p()) != Ryy:
        raise ValueError("The provided EID isn't a valid E2EE public key.")

    # Ensure y is even
    if Ry % 2 != 0:
        Ry = curve.p() - Ry

    return Ry


def encrypt_aes_eax(data: bytes, nonce: bytes, key: bytes) -> (bytes, bytes):
    # Ensure the key is 32 bytes for AES-256
    if len(key) != 32:
        raise ValueError("Key must be 32 bytes long for AES-256")

    # Create AES cipher in EAX mode
    cipher = AES.new(key, AES.MODE_EAX, nonce=nonce)

    # Encrypt the data
    ciphertext, tag = cipher.encrypt_and_digest(data)

    return ciphertext, tag


def decrypt_aes_eax(data: bytes, tag: bytes, nonce: bytes, key: bytes) -> bytes:
    # Ensure the key is 32 bytes for AES-256
    if len(key) != 32:
        raise ValueError("Key must be 32 bytes long for AES-256")

    # Create AES cipher in EAX mode
    cipher = AES.new(key, AES.MODE_EAX, nonce=nonce)

    # Decrypt the data
    return cipher.decrypt_and_verify(data, tag)


def encrypt(message: bytes, random: bytes, eid: bytes) -> (bytes, bytes):
    # Step 1: Choose a random number s in Fp
    curve = SECP160r1
    s = int.from_bytes(random, byteorder='big', signed=True) % curve.order

    # Step 2: Compute S = s * G
    S = s * curve.generator

    # Step 3: Compute R = (Rx, Ry) by substitution in the curve equation
    # and picking an arbitrary Ry value out of the possible results
    Rx = int.from_bytes(eid, byteorder='big')
    Ry = rx_to_ry(Rx, curve.curve)
    R = Point(curve.curve, Rx, Ry)

    # Step 4: Compute the 256-bit AES key k = HKDF-SHA256((s * R)x)
    # where (s * R)x is the x coordinate of the curve multiplication result.
    # Salt isn't specified.
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b'',
    )
    k = hkdf.derive((s * R).x().to_bytes(20, 'big'))

    # Step 5: Split Rx and Sx into lower 8 bytes
    LRx = Rx.to_bytes(20, 'big')[12:]
    LSx = S.x().to_bytes(20, 'big')[12:]

    # Step 6: Compute nonce
    nonce = LRx + LSx

    # Step 7: Encrypt the message m using AES-EAX-256
    m_dash, tag = encrypt_aes_eax(message, nonce, k)

    # Step 8: Result (m' || tag, Sx)
    return m_dash + tag, S.x().to_bytes(20, 'big')


def decrypt(identity_key: bytes, encryptedAndTag: bytes, Sx: bytes, beacon_time_counter: int) -> bytes:
    # Split into encrypted message and 16-byte tag
    m_dash = encryptedAndTag[:-16]
    tag = encryptedAndTag[-16:]

    # Given the beacon time counter value on which URx is based, compute the anticipated value of r
    curve = SECP160r1
    r = calculate_r(identity_key, beacon_time_counter)

    # Compute R = r * G
    R = r * curve.generator

    # Compute S = (Sx, Sy) by substitution in the curve equation and picking an arbitrary Sy value out of the
    # possible results.
    Sx = int.from_bytes(Sx, byteorder='big')
    Sy = rx_to_ry(Sx, curve.curve)
    S = Point(curve.curve, Sx, Sy)

    # Compute k = HKDF-SHA256((r * S)x) where (r * S)x is the x coordinate of the curve multiplication result.
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b''
    )
    k = hkdf.derive((r * S).x().to_bytes(20, 'big'))

    # Compute nonce = LRx || LSx.
    LRx = R.x().to_bytes(20, 'big')[12:]
    LSx = S.x().to_bytes(20, 'big')[12:]
    nonce = LRx + LSx

    # Compute m = AES-EAX-256-DEC(k, nonce, m’, tag)
    return decrypt_aes_eax(m_dash, tag, nonce, k)



