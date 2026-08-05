import base64
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

private_key = ec.generate_private_key(ec.SECP256R1())
public_key = private_key.public_key()

private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode("utf-8")

public_raw = public_key.public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint,
)

public_b64 = base64.urlsafe_b64encode(public_raw).rstrip(b"=").decode("ascii")

print("\nVAPID_PUBLIC_KEY:")
print(public_b64)
print("\nVAPID_PRIVATE_KEY:")
print(private_pem)
print("\nVAPID_SUBJECT:")
print("mailto:deine-email@example.com")
