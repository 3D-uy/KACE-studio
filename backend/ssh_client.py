import threading
import time
import paramiko
from typing import Callable

class SSHSession:
    def __init__(self):
        self.client = None
        self.channel = None
        self.running = False

    def connect(self, ip: str, username: str, password: str) -> bool:
        """
        Establishes an SSH connection to the target device with exponential backoff retries.
        Supports custom ports specified in the format IP:PORT (e.g. 127.0.0.1:2222).
        """
        retries = 5
        delay = 1
        max_delay = 5
        
        target_ip = ip
        port = 22
        if ":" in ip:
            parts = ip.split(":")
            target_ip = parts[0]
            try:
                port = int(parts[1])
            except ValueError:
                pass
        
        for attempt in range(retries + 1):
            try:
                self.client = paramiko.SSHClient()
                self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.client.connect(target_ip, port=port, username=username, password=password, timeout=5)
                return True
            except Exception as e:
                print(f"SSH connection attempt {attempt + 1}/{retries + 1} failed to {ip}: {e}")
                self.client = None
                if attempt < retries:
                    time.sleep(delay)
                    delay = min(delay * 2, max_delay)
                else:
                    return False

    def run_command_stream(self, command: str, on_data_callback: Callable[[str], None], on_close_callback: Callable[[], None]):
        """
        Executes a command on the remote Pi, streaming the stdout/stderr
        back asynchronously via the data callback.
        """
        if not self.client:
            on_data_callback("\r\n[SSH Connection Error]: Not connected.\r\n")
            on_close_callback()
            return
            
        def run():
            self.running = True
            try:
                transport = self.client.get_transport()
                self.channel = transport.open_session()
                # Request pty to ensure line-buffered and interactive streams function
                self.channel.get_pty(term='xterm')
                self.channel.exec_command(command)
                
                while self.running:
                    # Check if there is data to read
                    if self.channel.recv_ready():
                        data = self.channel.recv(4096)
                        if not data:
                            break
                        try:
                            text = data.decode("utf-8")
                        except UnicodeDecodeError:
                            text = data.decode("latin-1", errors="replace")
                        on_data_callback(text)
                    
                    # Check exit status if no more data is available
                    if self.channel.exit_status_ready() and not self.channel.recv_ready():
                        break
                        
            except Exception as e:
                on_data_callback(f"\r\n[SSH Stream Error]: {e}\r\n")
            finally:
                self.running = False
                if self.channel:
                    self.channel.close()
                on_close_callback()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

    def send_input(self, data: str):
        """
        Sends keyboard input to the running terminal channel.
        """
        if self.channel and not self.channel.closed:
            try:
                self.channel.send(data)
            except Exception as e:
                print(f"Error sending SSH input: {e}")

    def close(self):
        """
        Safely tears down active channels and connections.
        """
        self.running = False
        if self.channel:
            try:
                self.channel.close()
            except:
                pass
        if self.client:
            try:
                self.client.close()
            except:
                pass
        self.channel = None
        self.client = None

    def get_sftp(self):
        """Returns an open paramiko SFTP client, or None if not connected."""
        if self.client and self.client.get_transport() and self.client.get_transport().is_active():
            try:
                return self.client.open_sftp()
            except Exception:
                return None
        return None

    def list_directory(self, path: str) -> list:
        """Returns list of dicts: {name, is_dir, size, modified}"""
        sftp = self.get_sftp()
        if not sftp:
            return []
        try:
            import stat
            results = []
            for attr in sftp.listdir_attr(path):
                is_dir = stat.S_ISDIR(attr.st_mode)
                results.append({
                    "name": attr.filename,
                    "is_dir": is_dir,
                    "size": attr.st_size,
                    "modified": int(attr.st_mtime) if attr.st_mtime else 0
                })
            return results
        except Exception as e:
            print(f"SFTP list_directory error on path '{path}': {e}")
            return []
        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass

    def download_file(self, remote_path: str, local_path: str) -> bool:
        """Downloads a file from the Pi to local_path. Returns True on success."""
        sftp = self.get_sftp()
        if not sftp:
            return False
        try:
            import os
            # Ensure local parent directories exist
            local_dir = os.path.dirname(local_path)
            if local_dir:
                os.makedirs(local_dir, exist_ok=True)
            sftp.get(remote_path, local_path)
            return True
        except Exception as e:
            print(f"SFTP download_file error from '{remote_path}' to '{local_path}': {e}")
            return False
        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass
