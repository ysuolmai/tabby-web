import fsspec
import logging
import requests
import shutil
import subprocess
import tempfile
from django.core.management.base import BaseCommand
from django.conf import settings
from pathlib import Path
from urllib.parse import urlparse


def patch_browser_ssh_bundle(bundle_path):
    """Restore Ed25519 private-key support in ssh2's browser bundle."""
    source_path = bundle_path / "dist" / "index.js"
    source = source_path.read_text()
    old = """    return signature;
  },
  sendPacket: (proto, packet, bypass) => {"""
    new = """    if (signature && typeof signature.toBuffer === 'function') {
      return signature.toBuffer('raw');
    }
    return signature;
  },
  sendPacket: (proto, packet, bypass) => {"""
    if old not in source:
        raise RuntimeError(f"Unable to find ssh2 signature conversion in {source_path}")
    source = source.replace(old, new, 1)
    extra_patches = [
        (
            "const { eddsaSupported, SUPPORTED_CIPHER } = __webpack_require__(/*! ./constants.js */ 8683);",
            """const { eddsaSupported, SUPPORTED_CIPHER } = __webpack_require__(/*! ./constants.js */ 8683);
const nacl = __webpack_require__(/*! tweetnacl */ 3492);""",
            "tweetnacl import",
        ),
        (
            """    return function sign(data, algo) {
      const pem = this[SYM_PRIV_PEM];""",
            """    return function sign(data, algo) {
      if (this.type === 'ssh-ed25519' && this._edPrivateKey) {
        return Buffer.from(nacl.sign.detached(
          new Uint8Array(data),
          new Uint8Array(this._edPrivateKey)
        ));
      }
      const pem = this[SYM_PRIV_PEM];""",
            "browser Ed25519 signer",
        ),
        (
            """      let algo;
      let privPEM;""",
            """      let algo;
      let edPrivate;
      let privPEM;""",
            "Ed25519 private material storage",
        ),
        (
            """          if (edpriv === undefined || edpriv.length !== 64)
            return new Error('Malformed OpenSSH private key');

          pubPEM = genOpenSSLEdPub(edpub);""",
            """          if (edpriv === undefined || edpriv.length !== 64)
            return new Error('Malformed OpenSSH private key');
          edPrivate = edpriv;

          pubPEM = genOpenSSLEdPub(edpub);""",
            "Ed25519 private material capture",
        ),
        (
            """      keys.push(
        new OpenSSH_Private(type, privComment, privPEM, pubPEM, pubSSH, algo,
                            decrypted)
      );""",
            """      const key = new OpenSSH_Private(
        type, privComment, privPEM, pubPEM, pubSSH, algo, decrypted
      );
      key._edPrivateKey = edPrivate;
      keys.push(key);""",
            "Ed25519 private material attachment",
        ),
    ]
    for old, new, name in extra_patches:
        if old not in source:
            raise RuntimeError(f"Unable to find ssh2 {name} patch point in {source_path}")
        source = source.replace(old, new, 1)

    ed25519_gate = (
        "        case 'ssh-ed25519': {\n"
        "          if (!eddsaSupported)\n"
        "            return new Error("
        + chr(96)
        + "Unsupported OpenSSH private key type: ${type}"
        + chr(96)
        + ");"
    )
    if ed25519_gate not in source:
        raise RuntimeError(f"Unable to find ssh2 browser Ed25519 gate in {source_path}")
    source = source.replace(ed25519_gate, "        case 'ssh-ed25519': {", 1)
    source_path.write_text(source)
class Command(BaseCommand):
    help = "Downloads a new app version"

    def add_arguments(self, parser):
        parser.add_argument("version", type=str)

    def handle(self, *args, **options):
        version = options["version"]
        target = f"{settings.APP_DIST_STORAGE}/{version}"

        fs = fsspec.filesystem(urlparse(settings.APP_DIST_STORAGE).scheme)

        plugin_list = [
            "tabby-web-container",
            "tabby-core",
            "tabby-settings",
            "tabby-terminal",
            "tabby-ssh",
            "tabby-community-color-schemes",
            "tabby-serial",
            "tabby-telnet",
            "tabby-web",
            "tabby-web-demo",
        ]

        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            for plugin in plugin_list:
                logging.info(f"Resolving {plugin}@{version}")
                response = requests.get(f"{settings.NPM_REGISTRY}/{plugin}/{version}")
                response.raise_for_status()
                info = response.json()
                url = info["dist"]["tarball"]

                logging.info(f"Downloading {plugin}@{version} from {url}")
                response = requests.get(url)

                with tempfile.NamedTemporaryFile("wb") as f:
                    f.write(response.content)
                    f.flush()
                    plugin_final_target = Path(tempdir) / plugin

                    with tempfile.TemporaryDirectory() as extraction_tmp:
                        subprocess.check_call(
                            ["tar", "-xzf", f.name, "-C", str(extraction_tmp)]
                        )
                        shutil.move(
                            Path(extraction_tmp) / "package", plugin_final_target
                        )

                    if plugin == "tabby-ssh":
                        patch_browser_ssh_bundle(plugin_final_target)

            if fs.exists(target):
                fs.rm(target, recursive=True)
            fs.mkdir(target)
            fs.put(str(tempdir), target, recursive=True)
