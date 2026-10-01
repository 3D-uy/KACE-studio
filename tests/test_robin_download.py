"""Studio transfers the selected final path; it does not transform firmware."""
from unittest.mock import Mock
import pytest
from main import Api


@pytest.mark.parametrize('filename', ['Robin_nano.bin', 'Robin_nano35.bin', 'Robin_nano43.bin'])
def test_robin_download_preserves_final_name_and_session(filename):
    api = Api()
    api._ssh_gen = 7
    api._ssh = Mock()
    api._ssh.download_file.return_value = True
    api._window = Mock()
    api._window.create_file_dialog.return_value = ('C:/downloads/' + filename,)
    remote = '/home/kace/kace/deploy/reviewed/' + filename
    assert api.download_file(remote, 7)
    assert api._window.create_file_dialog.call_args.kwargs['save_filename'] == filename
    api._ssh.download_file.assert_called_once_with(remote, 'C:/downloads/' + filename)


def test_robin_download_does_not_cross_session_or_cancelled_dialog():
    api = Api()
    api._ssh_gen = 7
    api._ssh = Mock()
    api._window = Mock()
    remote = '/home/kace/kace/deploy/reviewed/Robin_nano35.bin'
    assert api.download_file(remote, 6) is False
    api._window.create_file_dialog.assert_not_called()
    api._window.create_file_dialog.return_value = None
    assert api.download_file(remote, 7) is False
    api._ssh.download_file.assert_not_called()
