import unittest

import analyze_capture


class MeshCryptoTests(unittest.TestCase):
    def test_mesh_profile_k2_vector(self):
        net_key = bytes.fromhex("7dd7364cd842ad18c17c2b820c84c3d6")
        nid, encryption_key, privacy_key = analyze_capture.k2(net_key)
        self.assertEqual(nid, 0x68)
        self.assertEqual(
            encryption_key.hex(), "0953fa93e7caac9638f58820220a398e"
        )
        self.assertEqual(
            privacy_key.hex(), "8b84eedec100067d670971dd2aa700cf"
        )

    def test_mesh_profile_k3_vector(self):
        net_key = bytes.fromhex("7dd7364cd842ad18c17c2b820c84c3d6")
        self.assertEqual(analyze_capture.k3(net_key).hex(), "3ecaff672f673370")


if __name__ == "__main__":
    unittest.main()
