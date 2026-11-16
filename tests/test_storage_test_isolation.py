"""The fail-closed test guard cannot silently call real host storage."""
from unittest.mock import Mock

import pytest

from conftest import GuardedImagerSubprocess


def test_guard_rejects_refresh_and_records_even_if_caller_catches_it():
    delegate, attempted = Mock(), []
    guarded = GuardedImagerSubprocess(delegate, attempted)
    command = ['powershell', '-Command', 'Update-HostStorageCache']
    with pytest.raises(AssertionError, match='explicit storage isolation'):
        guarded.run(command, capture_output=True)
    delegate.run.assert_not_called()
    assert attempted == [(command, {'capture_output': True})]


def test_guard_preserves_other_consumers_and_does_not_patch_shared_subprocess():
    delegate, attempted = Mock(), []
    original = delegate.run
    guarded = GuardedImagerSubprocess(delegate, attempted)
    assert guarded.run(['synthetic-readonly']) is original.return_value
    original.assert_called_once_with(['synthetic-readonly'])
    assert delegate.run is original and attempted == []
