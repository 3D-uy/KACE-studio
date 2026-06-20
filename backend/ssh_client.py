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
