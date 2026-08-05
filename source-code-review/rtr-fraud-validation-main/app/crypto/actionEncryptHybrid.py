import os
import logging
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

class ActionEncryptHybrid:
    
    def __init__(self, job_current_date_time=None, json_data_to_replace_in_command=None):
        self._job_current_date_time = job_current_date_time
        self._json_data_to_replace_in_command = json_data_to_replace_in_command

    # Path กุญแจชี้ไปที่ /apps/pgImporter/opt/key/public.pem เหมือนเดิม
    def run(self, task_id, source, destination, public_key_path='/apps/pgImporter/opt/key/public.pem'):
        logging.info(f"Task {task_id}: Start encrypting {source} to {destination}")
        
        try:
            # --- โค้ดเข้ารหัส ---
            with open(public_key_path, "rb") as key_file:
                public_key = serialization.load_pem_public_key(key_file.read())

            aes_key = os.urandom(32)
            iv = os.urandom(12)

            encrypted_aes_key = public_key.encrypt(
                aes_key,
                padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
            )

            encryptor = Cipher(algorithms.AES(aes_key), modes.GCM(iv)).encryptor()

            with open(source, "rb") as f_in, open(destination, "wb") as f_out:
                f_out.write(len(encrypted_aes_key).to_bytes(4, byteorder='big'))
                f_out.write(encrypted_aes_key)
                f_out.write(iv)
                while True:
                    chunk = f_in.read(64 * 1024)
                    if not chunk:
                        break
                    f_out.write(encryptor.update(chunk))
                encryptor.finalize()
                f_out.write(encryptor.tag)
                
            # *** ลบคำสั่ง os.remove(source) ออกไปแล้ว ***
            
            logging.info(f"Task {task_id}: Encryption Success! (Original file kept)")
            return True

        except Exception as e:
            logging.error(f"Task {task_id} Encryption Failed: {str(e)}")
            return False