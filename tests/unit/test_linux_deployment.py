import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]


class LinuxDeploymentTests(unittest.TestCase):
    def test_deploy_script_is_fail_safe_and_never_deletes_volumes(self):
        text = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("set -Eeuo pipefail", text)
        self.assertIn("docker compose", text)
        self.assertIn("config --quiet", text)
        self.assertIn("pull data-plane", text)
        self.assertIn("DEPLOY_BUILD_LOCAL", text)
        self.assertIn("docker load --input", text)
        self.assertIn("Application image archive checksum mismatch", text)
        self.assertIn("${!name:-", text)
        self.assertIn("did not remain stable after startup", text)
        self.assertIn('if [[ "$(uname -s)" == "Linux" ]]', text)
        self.assertIn('DEPLOY_MODE must be standalone or integrated', text)
        self.assertIn('REALTIME_E2E_ARMED=true is required', text)
        self.assertIn('IF05_E2E_ARMED=true is required', text)
        self.assertNotIn("down -v", text)
        self.assertNotIn("system prune", text)
        self.assertNotIn("volume rm", text)
        self.assertNotIn("erase", text.lower())

    def test_company_overlay_has_bounded_core_memory_and_optional_workers(self):
        text = (ROOT / "compose.deploy.yaml").read_text(encoding="utf-8")
        for service in ("postgres", "mqtt", "data-plane", "data-api"):
            match = re.search(rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|\Z)", text)
            self.assertIsNotNone(match, service)
            block = match.group(1)
            self.assertIn("mem_limit:", block, service)
            self.assertIn("cpus:", block, service)
        self.assertIn('profiles: ["integration"]', text)
        self.assertIn('profiles: ["realtime"]', text)
        self.assertIn("IF05_E2E_HIGH_WATER_ID", text)
        self.assertIn("127.0.0.1", text)
        self.assertNotIn("192.168.1.40", text)

    def test_environment_template_contains_no_live_credentials(self):
        text = (ROOT / "deploy.env.example").read_text(encoding="utf-8")
        self.assertIn("CHANGE_ME", text)
        self.assertNotIn("192.168.1.40", text)
        self.assertNotIn("semisweet-conceal-gizzard", text)

    def test_release_builder_excludes_secrets_and_uses_docker_save_output(self):
        text = (ROOT / "scripts" / "build-release-bundle.sh").read_text(encoding="utf-8")
        self.assertIn("docker save --output", text)
        self.assertIn("TARGET_PLATFORM", text)
        self.assertNotIn(".secrets/", text)
        self.assertNotIn("docker push", text)


if __name__ == "__main__":
    unittest.main()
