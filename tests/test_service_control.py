import unittest
from unittest.mock import patch

import service_control


class ServiceControlTests(unittest.TestCase):
    @patch.object(service_control.subprocess, 'run')
    def test_reads_installed_enabled_and_active_state(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = 'LoadState=loaded\nUnitFileState=enabled\nActiveState=active\n'
        self.assertEqual(service_control.status(), {
            'installed': True, 'enabled': True, 'active': True})
        self.assertEqual(run.call_args[0][0][0:2], ['/usr/bin/systemctl', 'show'])

    @patch.object(service_control.subprocess, 'run')
    def test_only_uses_fixed_helper_actions(self, run):
        run.return_value.returncode = 0
        service_control.set_enabled(True)
        self.assertEqual(run.call_args[0][0], [
            '/usr/bin/sudo', '-n', service_control.CONTROL_HELPER, 'enable'])
        service_control.set_enabled(False)
        self.assertEqual(run.call_args[0][0], [
            '/usr/bin/sudo', '-n', service_control.CONTROL_HELPER, 'disable'])


if __name__ == '__main__':
    unittest.main()
