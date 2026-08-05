import os
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

def decrypt_hybrid(file_byte_data, private_key):
    print(f"Start decrypting ...")
    
    # 1. โหลด Private Key (ถ้า Private Key มีพาสเวิร์ด ให้ใส่ที่ password=b"your_password")
    private_key = private_key.replace("\\n", "\n")

    private_key = serialization.load_pem_private_key(
        private_key.encode(),   # convert string → bytes
        password=None
    )
    
    file_byte_data.seek(0, os.SEEK_END)
    file_size = file_byte_data.tell()
    file_byte_data.seek(0)

    # 2. อ่าน 4 ไบต์แรก เพื่อดูว่ากุญแจ AES ที่เข้ารหัสมา มีขนาดกี่ไบต์
    key_len_bytes = file_byte_data.read(4)
    key_length = int.from_bytes(key_len_bytes, byteorder='big')

    # 3. อ่านกุญแจ AES ที่ถูกเข้ารหัส (Encrypted AES Key) ตามขนาดที่ได้มา
    encrypted_aes_key = file_byte_data.read(key_length)

    # 4. อ่านค่า IV (12 ไบต์) สำหรับ AES-GCM
    iv = file_byte_data.read(12)

    # 5. ข้ามไปอ่าน Tag (16 ไบต์สุดท้ายของไฟล์) เพื่อใช้ตรวจสอบความถูกต้อง
    file_byte_data.seek(-16, os.SEEK_END)
    tag = file_byte_data.read(16)

    # 6. ใช้ Private Key ถอดรหัสเพื่อเอากุญแจ AES-256 ตัวจริงออกมา
    aes_key = private_key.decrypt(
        encrypted_aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )

    # 7. กลับชี้ตำแหน่งไปที่จุดเริ่มต้นของเนื้อหาไฟล์ (Ciphertext)
    # ตำแหน่ง = 4 (ความยาว) + key_length (กุญแจ) + 12 (IV)
    start_of_ciphertext = 4 + key_length + 12
    file_byte_data.seek(start_of_ciphertext)
    
    # คำนวณขนาดของเนื้อหาไฟล์ (ลบ Header ด้านหน้า และลบ Tag 16 ไบต์ด้านหลังออก)
    ciphertext_length = file_size - start_of_ciphertext - 16

    # 8. ตั้งค่าตัวถอดรหัส AES-GCM
    decryptor = Cipher(algorithms.AES(aes_key), modes.GCM(iv, tag)).decryptor()

    # 9. ทยอยอ่านข้อมูลทีละ Chunk และถอดรหัสกลับเป็นไฟล์ .txt
    bytes_read = 0
    chunk_size = 64 * 1024  # อ่านทีละ 64KB (จัดการไฟล์ใหญ่ได้สบายๆ)
    
    plaintext = b""

    while bytes_read < ciphertext_length:
        read_size = min(chunk_size, ciphertext_length - bytes_read)
        chunk = file_byte_data.read(read_size)

        if not chunk:
            break

        plaintext += decryptor.update(chunk)
        bytes_read += len(chunk)

    plaintext += decryptor.finalize()

    return plaintext

