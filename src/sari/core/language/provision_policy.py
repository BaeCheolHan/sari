"""LSP 런타임 프로비저닝 정책 SSOT를 제공한다."""

from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class LspProvisionPolicyDTO:
    """언어별 LSP 프로비저닝 정책 DTO다."""

    language: str
    provisioning_mode: str
    install_hint: str


_POLICY_BY_LANGUAGE: dict[str, LspProvisionPolicyDTO] = {
    "python": LspProvisionPolicyDTO(
        language="python",
        provisioning_mode="hybrid",
        install_hint="python 환경에 pyright 모듈이 필요합니다. 예: pip install pyright",
    ),
    "java": LspProvisionPolicyDTO(
        language="java",
        provisioning_mode="auto_provision",
        install_hint="jdtls 런타임은 자동 다운로드됩니다.",
    ),
    "kotlin": LspProvisionPolicyDTO(
        language="kotlin",
        provisioning_mode="auto_provision",
        install_hint="kotlin-language-server 런타임은 자동 다운로드되며 JDK가 필요합니다.",
    ),
    "groovy": LspProvisionPolicyDTO(
        language="groovy",
        provisioning_mode="auto_provision",
        install_hint="groovy-language-server 런타임은 자동 다운로드되며 JDK가 필요합니다.",
    ),
    "typescript": LspProvisionPolicyDTO(
        language="typescript",
        provisioning_mode="requires_system_binary",
        install_hint="node/npm 및 typescript-language-server가 필요합니다. 예: npm i -g typescript-language-server typescript",
    ),
    "vue": LspProvisionPolicyDTO(
        language="vue",
        provisioning_mode="requires_system_binary",
        install_hint="node/npm 및 @vue/language-server가 필요합니다. 예: npm i -g @vue/language-server",
    ),
    "yaml": LspProvisionPolicyDTO(
        language="yaml",
        provisioning_mode="requires_system_binary",
        install_hint="node/npm 및 yaml-language-server가 필요합니다.",
    ),
    "bash": LspProvisionPolicyDTO(
        language="bash",
        provisioning_mode="requires_system_binary",
        install_hint="node/npm 및 bash-language-server가 필요합니다. 예: npm i -g bash-language-server",
    ),
    "go": LspProvisionPolicyDTO(
        language="go",
        provisioning_mode="requires_system_binary",
        install_hint="gopls가 필요합니다. 예: go install golang.org/x/tools/gopls@latest (설치 후 GOPATH/bin 또는 ~/go/bin을 PATH에 포함)",
    ),
    "rust": LspProvisionPolicyDTO(
        language="rust",
        provisioning_mode="requires_system_binary",
        install_hint="rust-analyzer가 필요합니다.",
    ),
    "cpp": LspProvisionPolicyDTO(
        language="cpp",
        provisioning_mode="requires_system_binary",
        install_hint="clangd가 필요합니다.",
    ),
    "csharp": LspProvisionPolicyDTO(
        language="csharp",
        provisioning_mode="hybrid",
        install_hint="dotnet SDK가 필요하며 csharp-ls/omnisharp 런타임은 설정에 따라 자동 구성됩니다.",
    ),
    "ruby": LspProvisionPolicyDTO(
        language="ruby",
        provisioning_mode="requires_system_binary",
        install_hint="ruby-lsp가 필요합니다. 예: gem install ruby-lsp",
    ),
    "php": LspProvisionPolicyDTO(
        language="php",
        provisioning_mode="requires_system_binary",
        install_hint="intelephense 또는 phpactor가 필요합니다.",
    ),
    "perl": LspProvisionPolicyDTO(
        language="perl",
        provisioning_mode="requires_system_binary",
        install_hint="Perl language server가 필요합니다.",
    ),
    "clojure": LspProvisionPolicyDTO(
        language="clojure",
        provisioning_mode="requires_system_binary",
        install_hint="clojure CLI와 clojure-lsp가 필요합니다.",
    ),
    "elixir": LspProvisionPolicyDTO(
        language="elixir",
        provisioning_mode="requires_system_binary",
        install_hint="expert(Elixir LS)와 Elixir/Erlang 런타임이 필요합니다.",
    ),
    "elm": LspProvisionPolicyDTO(
        language="elm",
        provisioning_mode="requires_system_binary",
        install_hint="node/npm 및 elm-language-server가 필요합니다.",
    ),
    "terraform": LspProvisionPolicyDTO(
        language="terraform",
        provisioning_mode="hybrid",
        install_hint="terraform/terraform-ls가 필요하며 일부 런타임은 자동 구성될 수 있습니다.",
    ),
    "swift": LspProvisionPolicyDTO(
        language="swift",
        provisioning_mode="requires_system_binary",
        install_hint="sourcekit-lsp와 Swift toolchain이 필요합니다.",
    ),
    "r": LspProvisionPolicyDTO(
        language="r",
        provisioning_mode="requires_system_binary",
        install_hint="R 언어 서버가 필요합니다.",
    ),
    "zig": LspProvisionPolicyDTO(
        language="zig",
        provisioning_mode="requires_system_binary",
        install_hint="zls가 필요합니다.",
    ),
    "lua": LspProvisionPolicyDTO(
        language="lua",
        provisioning_mode="requires_system_binary",
        install_hint="lua-language-server가 필요합니다.",
    ),
    "nix": LspProvisionPolicyDTO(
        language="nix",
        provisioning_mode="requires_system_binary",
        install_hint="nix 및 nixd가 필요합니다.",
    ),
    "dart": LspProvisionPolicyDTO(
        language="dart",
        provisioning_mode="requires_system_binary",
        install_hint="Dart SDK가 필요합니다.",
    ),
    "erlang": LspProvisionPolicyDTO(
        language="erlang",
        provisioning_mode="requires_system_binary",
        install_hint="erlang_ls가 필요합니다.",
    ),
    "scala": LspProvisionPolicyDTO(
        language="scala",
        provisioning_mode="requires_system_binary",
        install_hint="JDK 17+와 coursier(cs), Metals가 필요합니다.",
    ),
    "al": LspProvisionPolicyDTO(
        language="al",
        provisioning_mode="auto_provision",
        install_hint="AL language server 런타임은 자동 다운로드됩니다.",
    ),
    "fsharp": LspProvisionPolicyDTO(
        language="fsharp",
        provisioning_mode="requires_system_binary",
        install_hint="dotnet SDK와 F# language server가 필요합니다.",
    ),
    "rego": LspProvisionPolicyDTO(
        language="rego",
        provisioning_mode="requires_system_binary",
        install_hint="regal 서버가 필요합니다.",
    ),
    "markdown": LspProvisionPolicyDTO(
        language="markdown",
        provisioning_mode="auto_provision",
        install_hint="marksman 런타임은 자동 다운로드됩니다.",
    ),
    "julia": LspProvisionPolicyDTO(
        language="julia",
        provisioning_mode="requires_system_binary",
        install_hint="julia 실행 파일과 LanguageServer.jl 환경이 필요합니다.",
    ),
    "fortran": LspProvisionPolicyDTO(
        language="fortran",
        provisioning_mode="requires_system_binary",
        install_hint="fortls가 필요합니다.",
    ),
    "haskell": LspProvisionPolicyDTO(
        language="haskell",
        provisioning_mode="requires_system_binary",
        install_hint="haskell-language-server-wrapper가 필요합니다.",
    ),
    "powershell": LspProvisionPolicyDTO(
        language="powershell",
        provisioning_mode="requires_system_binary",
        install_hint="pwsh와 PowerShell language server가 필요합니다.",
    ),
    "pascal": LspProvisionPolicyDTO(
        language="pascal",
        provisioning_mode="hybrid",
        install_hint="pasls는 자동 다운로드 가능하며 FPC 설정을 권장합니다.",
    ),
    "matlab": LspProvisionPolicyDTO(
        language="matlab",
        provisioning_mode="hybrid",
        install_hint="MATLAB(R2021b+) 및 node가 필요하며 MATLAB LS 런타임은 자동 다운로드됩니다.",
    ),
    "toml": LspProvisionPolicyDTO(
        language="toml",
        provisioning_mode="hybrid",
        install_hint="taplo는 자동 다운로드 또는 시스템 바이너리 사용이 가능합니다.",
    ),
}


def get_lsp_provision_policy(language: str) -> LspProvisionPolicyDTO:
    """언어별 LSP 프로비저닝 정책을 반환한다."""
    normalized = language.strip().lower()
    if normalized == "python":
        provider = os.environ.get("SARI_PYTHON_LSP_PROVIDER", "").strip().lower()
        if provider == "pyrefly":
            return LspProvisionPolicyDTO(
                language="python",
                provisioning_mode="hybrid",
                install_hint="python 환경에 pyrefly가 필요합니다. 예: pip install pyrefly",
            )
    policy = _POLICY_BY_LANGUAGE.get(normalized)
    if policy is not None:
        return policy
    return LspProvisionPolicyDTO(
        language=normalized,
        provisioning_mode="requires_system_binary",
        install_hint=f"{normalized} 언어 서버 설치가 필요합니다.",
    )
