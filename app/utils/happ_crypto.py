import base64
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

# Happ Public Key V3
HAPP_PUBLIC_KEY_V3_PEM = b"""-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAlBetA0wjbaj+h7oJ/d/h
pNrXvAcuhOdFGEFcfCxSWyLzWk4SAQ05gtaEGZyetTax2uqagi9HT6lapUSUe2S8
nMLJf5K+LEs9TYrhhBdx/B0BGahA+lPJa7nUwp7WfUmSF4hir+xka5ApHjzkAQn6
cdG6FKtSPgq1rYRPd1jRf2maEHwiP/e/jqdXLPP0SFBjWTMt/joUDgE7v/IGGB0L
Q7mGPAlgmxwUHVqP4bJnZ//5sNLxWMjtYHOYjaV+lixNSfhFM3MdBndjpkmgSfmg
D5uYQYDL29TDk6Eu+xetUEqry8ySPjUbNWdDXCglQWMxDGjaqYXMWgxBA1UKjUBW
wbgr5yKTJ7mTqhlYEC9D5V/LOnKd6pTSvaMxkHXwk8hBWvUNWAxzAf5JZ7EVE3jt
0j682+/hnmL/hymUE44yMG1gCcWvSpB3BTlKoMnl4yrTakmdkbASeFRkN3iMRewa
IenvMhzJh1fq7xwX94otdd5eLB2vRFavrnhOcN2JJAkKTnx9dwQwFpGEkg+8U613
+Tfm/f82l56fFeoFN98dD2mUFLFZoeJ5CG81ZeXrH83niI0joX7rtoAZIPWzq3Y1
Zb/Zq+kK2hSIhphY172Uvs8X2Qp2ac9UoTPM71tURsA9IvPNvUwSIo/aKlX5KE3I
VE0tje7twWXL5Gb1sfcXRzsCAwEAAQ==
-----END PUBLIC KEY-----"""

def create_happ_crypto_link(subscription_url: str) -> str:
    """
    Generates a Happ crypto link locally using RSA encryption.
    Matches the logic of createHappCryptoLink from the backend.
    """
    if not subscription_url:
        return ""

    try:
        # Load the public key
        public_key = serialization.load_pem_public_key(
            HAPP_PUBLIC_KEY_V3_PEM,
            backend=default_backend()
        )

        # Encrypt the content (subscription link)
        # The backend uses RSA_PKCS1_PADDING (padding: 1 in Node's publicEncrypt)
        encrypted = public_key.encrypt(
            subscription_url.encode('utf-8'),
            padding.PKCS1v15()
        )

        # Base64 encode the result
        encrypted_b64 = base64.b64encode(encrypted).decode('utf-8')

        # Add prefix
        return f"happ://crypt3/{encrypted_b64}"

    except Exception as e:
        # In case of any error (e.g. invalid key, library issue), return empty string
        # to avoid crashing the flow
        return ""
