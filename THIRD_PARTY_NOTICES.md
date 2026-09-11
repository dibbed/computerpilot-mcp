# Third-Party Notices

This project bundles and redistributes third-party software components. This document provides attribution and licensing notices for these components.

The project as a whole is licensed under the Apache License, Version 2.0 (Copyright 2026 Ali Khalili). However, Ali Khalili does **not** claim ownership or authorship of bundled third-party binaries and dependencies.

---

## 1. OpenAI Tunnel Client (`tunnel-client.exe`)

- **Component**: `tunnel-client.exe` (version `0.0.11+8d55683eeef80bc5e360d95abf4692454fafc615`)
- **Author**: OpenAI
- **Upstream Project**: [https://github.com/openai/tunnel-client](https://github.com/openai/tunnel-client)
- **License**: Apache License, Version 2.0
- **Notice File**: [third_party/tunnel-client/NOTICE](third_party/tunnel-client/NOTICE)
- **License File**: [third_party/tunnel-client/LICENSE](third_party/tunnel-client/LICENSE)

```
Copyright 2026 OpenAI

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

---

## 2. Cloudflare Tunnel Client (`cloudflared.exe`)

- **Component**: `cloudflared.exe` (version `2026.7.2`)
- **Author**: Cloudflare, Inc.
- **Upstream Project**: [https://github.com/cloudflare/cloudflared](https://github.com/cloudflare/cloudflared)
- **Manifest**: `cloudflared-manifest.json`
- **License**: Apache License, Version 2.0
- **License File**: [third_party/cloudflared/LICENSE](third_party/cloudflared/LICENSE)

```
Copyright (c) Cloudflare, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

---

## 3. Python Runtime Dependencies

The project relies on external Python packages defined in `requirements.txt` and `requirements-browser.txt`. Each dependency is subject to its respective open-source license:

- `mcp`: MIT License
- `pydantic`: MIT License
- `psutil`: BSD-3-Clause License
- `charset-normalizer`: MIT License
- `Pillow`: HPND License
- `pytest`: MIT License
- `ruff`: MIT / Apache-2.0 License
- `mypy`: MIT License
- `playwright` (optional): Apache-2.0 License
