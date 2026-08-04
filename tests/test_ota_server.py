import unittest

from ota_center import OTARequestHandler


class OTAServerTests(unittest.TestCase):
    def tearDown(self):
        OTARequestHandler.setup_progress("", 0)

    def test_http服务只允许访问当前固件(self):
        OTARequestHandler.setup_progress("firmware.bin", 128)

        self.assertTrue(OTARequestHandler.is_allowed_path("/firmware.bin"))
        self.assertFalse(OTARequestHandler.is_allowed_path("/firmware%20name.bin"))
        self.assertFalse(OTARequestHandler.is_allowed_path("/"))
        self.assertFalse(OTARequestHandler.is_allowed_path("/other.bin"))


if __name__ == "__main__":
    unittest.main()
