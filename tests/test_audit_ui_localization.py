"""Critical labels and recoverable errors use the existing locale catalogs."""
from pathlib import Path
import json
import re
import shutil
import subprocess
import pytest


@pytest.mark.parametrize("locale, html_language", [("en-US","en"),("es-UY","es"),("pt-BR","pt")])
def test_critical_labels_error_codes_and_document_language(locale, html_language):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for frontend execution")
    root = Path(__file__).resolve().parents[1]
    app = (root / "web/app.js").read_text(encoding="utf-8")
    start = app.index("const STUDIO_INSTALLATION_TEXT =")
    end = app.index("window.showProvisioningError =", start)
    source = app[start:end]
    keys = re.findall(r'data-ui-(?:text|label|placeholder)="([^"]+)"', (root / "web/index.html").read_text(encoding="utf-8"))
    script = "const assert=require('node:assert/strict'); const navigator={language:" + json.dumps(locale) + "}; const document={documentElement:{},getElementById(){return null;},addEventListener(_name,callback){globalThis.loaded=callback;}};" + source
    script += "loaded(); assert.equal(document.documentElement.lang," + json.dumps(html_language) + ");"
    script += "for(const language of Object.keys(STUDIO_INSTALLATION_TEXT)){for(const key of " + json.dumps(keys + ["emptyDirectory", "wifiParserUnsupported", "sftpPermissionDenied", "discoveryAmbiguous", "accountPasswordInvalid"]) + "){assert.equal(typeof studioText(key,language),'string',key); assert.ok(studioText(key,language).length);}}"
    if html_language != "en":
        script += "assert.notEqual(studioText('accountPasswordInvalid'),studioText('accountPasswordInvalid','English'));"
    result = subprocess.run([node, "-"], input=script, text=True, encoding="utf-8", capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
