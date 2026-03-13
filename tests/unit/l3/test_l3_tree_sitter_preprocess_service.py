from __future__ import annotations

from pathlib import Path

from sari.services.collection.l3.l3_tree_sitter_outline import TreeSitterOutlineResult
from sari.services.collection.l3.l3_treesitter_preprocess_service import (
    L3PreprocessDecision,
    L3TreeSitterPreprocessService,
    _extract_outline_subinterp_task,
)


def test_scala_preprocess_regex_fallback_extracts_symbols_when_tree_sitter_degraded() -> None:
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = """
package com.acme.core

final class UserService {
  def createUser(name: String): Unit = {}
  val version = "1.0"
}
"""

    result = service.preprocess(relative_path="src/main/scala/com/acme/UserService.scala", content_text=content)

    names = {str(item.get("name")) for item in result.symbols}
    kinds = {str(item.get("kind")) for item in result.symbols}
    assert result.source == "regex_outline"
    assert result.decision in (L3PreprocessDecision.L3_ONLY, L3PreprocessDecision.NEEDS_L5)
    assert "com.acme.core" in names
    assert "UserService" in names
    assert "createUser" in names
    assert "version" in names
    assert {"module", "class", "method", "field"}.issubset(kinds)


def test_scala_preprocess_marks_heavy_file_as_deferred() -> None:
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = "class A {}\n"
    huge = content * 20000

    result = service.preprocess(
        relative_path="src/main/scala/com/acme/Huge.scala",
        content_text=huge,
        max_bytes=1024,
    )

    assert result.degraded is True
    assert result.decision == L3PreprocessDecision.DEFERRED_HEAVY
    assert result.reason == "l3_preprocess_large_file"


def test_preprocess_forwards_incremental_parse_key_to_outline_extractor() -> None:
    class _StubExtractor:
        def __init__(self) -> None:
            self.last_kwargs: dict[str, object] = {}

        def is_available_for(self, lang_key: str) -> bool:
            _ = lang_key
            return True

        def extract_outline(self, **kwargs):  # noqa: ANN003
            self.last_kwargs = dict(kwargs)
            return TreeSitterOutlineResult(
                symbols=[{"name": "A", "kind": "class", "line": 1, "end_line": 1}],
                degraded=False,
            )

    extractor = _StubExtractor()
    service = L3TreeSitterPreprocessService(
        tree_sitter_enabled=True,
        tree_sitter_outline_extractor=extractor,  # type: ignore[arg-type]
    )
    _ = service.preprocess(
        relative_path="src/main/kotlin/A.kt",
        content_text="class A {}",
        repo_root="/tmp/repo",
    )
    assert extractor.last_kwargs.get("parse_key") == "/tmp/repo::src/main/kotlin/A.kt"


def test_preprocess_supports_legacy_outline_extractor_signature_without_parse_key() -> None:
    class _LegacyExtractor:
        def is_available_for(self, lang_key: str) -> bool:
            _ = lang_key
            return True

        def extract_outline(self, *, lang_key: str, content_text: str, budget_sec: float):  # noqa: ANN001
            _ = (lang_key, content_text, budget_sec)
            return TreeSitterOutlineResult(
                symbols=[{"name": "A", "kind": "class", "line": 1, "end_line": 1}],
                degraded=False,
            )

    service = L3TreeSitterPreprocessService(
        tree_sitter_enabled=True,
        tree_sitter_outline_extractor=_LegacyExtractor(),  # type: ignore[arg-type]
    )
    result = service.preprocess(
        relative_path="src/main/java/A.java",
        content_text="class A {}",
        repo_root="/tmp/repo",
    )
    assert result.source == "tree_sitter_outline"


def test_preprocess_uses_subinterpreter_path_when_enabled_and_payload_large_enough() -> None:
    class _Executor:
        def __init__(self) -> None:
            self.called = 0

        def submit(self, fn, *args, **kwargs):  # noqa: ANN001, ANN003
            self.called += 1

            class _Future:
                def result(self, timeout=None):  # noqa: ANN001
                    _ = timeout
                    return {
                        "symbols": [{"name": "Sub", "kind": "class", "line": 1, "end_line": 1}],
                        "degraded": False,
                        "reason": None,
                    }

            return _Future()

    class _StubExtractor:
        def is_available_for(self, lang_key: str) -> bool:
            _ = lang_key
            return True

        def extract_outline(self, **kwargs):  # noqa: ANN003
            raise AssertionError("inline path must not run when subinterp succeeds")

    service = L3TreeSitterPreprocessService(
        tree_sitter_enabled=True,
        tree_sitter_outline_extractor=_StubExtractor(),  # type: ignore[arg-type]
        tree_sitter_executor_mode="subinterp",
        tree_sitter_subinterp_min_bytes=1,
    )
    service._tree_sitter_subinterp_executor = _Executor()  # type: ignore[assignment]
    service._tree_sitter_executor_mode = "subinterp"  # type: ignore[assignment]
    result = service.preprocess(
        relative_path="src/main/kotlin/A.kt",
        content_text="class A {}",
        repo_root="/tmp/repo",
    )
    assert result.source == "tree_sitter_outline"
    assert result.symbols[0]["name"] == "Sub"
    assert service._tree_sitter_subinterp_executor.called == 1  # type: ignore[union-attr]


def test_preprocess_falls_back_to_inline_when_subinterp_reports_tree_sitter_unavailable() -> None:
    class _Executor:
        def submit(self, fn, *args, **kwargs):  # noqa: ANN001, ANN003
            class _Future:
                def result(self, timeout=None):  # noqa: ANN001
                    _ = timeout
                    return {
                        "symbols": [],
                        "degraded": True,
                        "reason": "tree_sitter_unavailable:ImportError",
                    }

            return _Future()

    class _StubExtractor:
        def is_available_for(self, lang_key: str) -> bool:
            _ = lang_key
            return True

        def extract_outline(self, **kwargs):  # noqa: ANN003
            return TreeSitterOutlineResult(
                symbols=[{"name": "Inline", "kind": "class", "line": 1, "end_line": 1}],
                degraded=False,
            )

    service = L3TreeSitterPreprocessService(
        tree_sitter_enabled=True,
        tree_sitter_outline_extractor=_StubExtractor(),  # type: ignore[arg-type]
        tree_sitter_executor_mode="subinterp",
        tree_sitter_subinterp_min_bytes=1,
    )
    service._tree_sitter_subinterp_executor = _Executor()  # type: ignore[assignment]
    service._tree_sitter_executor_mode = "subinterp"  # type: ignore[assignment]

    result = service.preprocess(
        relative_path="src/main/kotlin/A.kt",
        content_text="class A {}",
        repo_root="/tmp/repo",
    )

    assert result.source == "tree_sitter_outline"
    assert result.symbols[0]["name"] == "Inline"


def test_preprocess_routes_sari_cli_main_to_l5_for_call_rich_python_file() -> None:
    service = L3TreeSitterPreprocessService()
    repo_root = Path(__file__).resolve().parents[3]
    relative_path = "src/sari/cli/main.py"
    content = (repo_root / relative_path).read_text(encoding="utf-8")

    result = service.preprocess(
        relative_path=relative_path,
        content_text=content,
        repo_root=str(repo_root),
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_routes_call_rich_java_file_to_l5() -> None:
    service = L3TreeSitterPreprocessService()
    content = """
package com.acme.pay;

public class InicisHttpClient {
    private final String formPayHost;
    private final String formPayIp;
    private final String formPayMid;

    public void convertToMultiValueMap(String value) {
        addIfNotNull("type", value);
        addIfNotNull("mid", value);
        addIfNotNull("hashData", createHash(value));
        createAuditLog(value);
    }

    private void addIfNotNull(String key, String value) {
        if (value == null) {
            return;
        }
        createHash(key + value);
        normalizeKey(key);
    }

    private String createHash(String value) {
        return value;
    }

    private String normalizeKey(String key) {
        return key.toLowerCase();
    }

    private void createAuditLog(String value) {
        createPayload(value);
    }

    private String createPayload(String value) {
        return formPayHost + formPayIp + formPayMid + value;
    }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/pay/InicisHttpClient.java",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_java_class_qualified_static_calls_on_l3_only() -> None:
    service = L3TreeSitterPreprocessService()
    content = """
package com.acme.pay;

public class PaymentClient {
    static String normalizeOne(String value) {
        return PaymentClient.normalizeTwo(value);
    }

    static String normalizeTwo(String value) {
        return PaymentClient.normalizeThree(value);
    }

    static String normalizeThree(String value) {
        return PaymentClient.normalizeFour(value);
    }

    static String normalizeFour(String value) {
        return PaymentClient.normalizeFive(value);
    }

    static String normalizeFive(String value) {
        return PaymentClient.normalizeSix(value);
    }

    static String normalizeSix(String value) {
        return value;
    }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/pay/PaymentClient.java",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_java_regex_fallback_does_not_extract_wrapped_return_calls_as_methods() -> None:
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = """
package com.acme.pay;

class PaymentClient {
    String render(String value) {
        return createHash(
            value
        );
    }

    String createHash(String value) {
        return value;
    }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/pay/PaymentClient.java",
        content_text=content,
    )

    method_names = [str(item.get("name")) for item in result.symbols if str(item.get("kind")) == "method"]
    assert method_names.count("createHash") == 1


def test_preprocess_keeps_python_classmethod_calls_on_l3_only_without_tree_sitter() -> None:
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = """
class TaskService:
    @classmethod
    def step_one(cls):
        return cls.step_two()

    @classmethod
    def step_two(cls):
        return cls.step_three()

    @classmethod
    def step_three(cls):
        return cls.step_four()

    @classmethod
    def step_four(cls):
        return cls.step_five()

    @classmethod
    def step_five(cls):
        return cls.step_six()

    @classmethod
    def step_six(cls):
        return None
"""

    result = service.preprocess(
        relative_path="src/app/task_service.py",
        content_text=content,
        repo_root="/tmp/repo",
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_call_rich_java_file_on_l3_only_without_tree_sitter() -> None:
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = """
package com.acme.pay;

public class InicisHttpClient {
    private final String formPayHost;
    private final String formPayIp;
    private final String formPayMid;

    public void convertToMultiValueMap(String value) {
        addIfNotNull("type", value);
        addIfNotNull("mid", value);
        addIfNotNull("hashData", createHash(value));
        createAuditLog(value);
    }

    private void addIfNotNull(String key, String value) {
        if (value == null) {
            return;
        }
        createHash(key + value);
        normalizeKey(key);
    }

    private String createHash(String value) {
        return value;
    }

    private String normalizeKey(String key) {
        return key.toLowerCase();
    }

    private void createAuditLog(String value) {
        createPayload(value);
    }

    private String createPayload(String value) {
        return formPayHost + formPayIp + formPayMid + value;
    }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/pay/InicisHttpClient.java",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_python_settings_file_on_l3_only() -> None:
    service = L3TreeSitterPreprocessService()
    content = """
class AppSettings:
    def host(self) -> str:
        return "localhost"

    def port(self) -> int:
        return 8080

    def debug(self) -> bool:
        return False

    def region(self) -> str:
        return "kr"
"""

    result = service.preprocess(
        relative_path="src/app/settings.py",
        content_text=content,
        repo_root="/tmp/repo",
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_python_config_file_on_l3_only_even_when_call_rich() -> None:
    service = L3TreeSitterPreprocessService()
    content = """
class AppConfig:
    def build_host(self) -> str:
        return self.normalize("localhost")

    def build_port(self) -> int:
        return self.default_port()

    def build_debug(self) -> bool:
        return self.default_debug()

    def build_region(self) -> str:
        return self.normalize("kr")

    def normalize(self, value: str) -> str:
        return value.strip()

    def default_port(self) -> int:
        return 8080

    def default_debug(self) -> bool:
        return False
"""

    result = service.preprocess(
        relative_path="src/sari/core/config_model.py",
        content_text=content,
        repo_root="/tmp/repo",
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_async_definition_only_python_file_on_l3_only() -> None:
    service = L3TreeSitterPreprocessService()
    content = """
async def fetch_user():
    return None

async def fetch_account():
    return None

async def fetch_orders():
    return None

async def fetch_events():
    return None

async def fetch_payments():
    return None

async def fetch_notifications():
    return None
"""

    result = service.preprocess(
        relative_path="src/app/async_handlers.py",
        content_text=content,
        repo_root="/tmp/repo",
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_python_docstring_call_text_on_l3_only() -> None:
    service = L3TreeSitterPreprocessService()
    content = '''"""
Example:
run_task()
handle_task()
"""

def run_task():
    return None

def handle_task():
    return None

def build_task():
    return None

def format_task():
    return None

def load_task():
    return None

def save_task():
    return None
'''

    result = service.preprocess(
        relative_path="src/app/task_service.py",
        content_text=content,
        repo_root="/tmp/repo",
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_python_class_instantiation_only_file_on_l3_only() -> None:
    service = L3TreeSitterPreprocessService()
    content = """
class Worker:
    def run(self):
        return None


def build_worker():
    return Worker()


def clone_worker():
    return Worker()


def load_worker():
    return Worker()


def save_worker():
    return Worker()


def format_worker():
    return Worker()
"""

    result = service.preprocess(
        relative_path="src/app/worker_service.py",
        content_text=content,
        repo_root="/tmp/repo",
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_python_qualified_calls_on_l3_only() -> None:
    service = L3TreeSitterPreprocessService()
    content = """
def load():
    return None


def format():
    return None


def build():
    return json.load(stream)


def render():
    return formatter.format(value)


def parse():
    return json.load(stream)


def emit():
    return formatter.format(value)
"""

    result = service.preprocess(
        relative_path="src/app/load_service.py",
        content_text=content,
        repo_root="/tmp/repo",
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_python_class_heavy_service_file_on_l3_only_when_callable_count_low() -> None:
    service = L3TreeSitterPreprocessService()
    content = """
class Alpha:
    pass

class Beta:
    pass

class Gamma:
    pass

class Delta:
    pass

class Epsilon:
    pass

def helper():
    return helper()
"""

    result = service.preprocess(
        relative_path="src/app/task_service.py",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_skips_ast_call_scan_for_relaxed_python_filename() -> None:
    service = L3TreeSitterPreprocessService()
    content = """
class ConfigA:
    pass

def helper_one():
    return helper_two()

def helper_two():
    return None

def helper_three():
    return None

def helper_four():
    return None

def helper_five():
    return None

def helper_six():
    return None
"""

    result = service.preprocess(
        relative_path="src/sari/core/config_model.py",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_skips_ast_call_scan_for_low_callable_java_file() -> None:
    service = L3TreeSitterPreprocessService()
    content = """
package com.acme.pay;

public class PaymentClient {
    private String host;
    private String mid;
    private String region;
    private String tenant;

    String helper() {
        return helper();
    }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/pay/PaymentClient.java",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_routes_java_overloaded_same_file_calls_to_l5() -> None:
    service = L3TreeSitterPreprocessService()
    content = """
package com.acme.pay;

public class PaymentClient {
    String normalize(String value) {
        return normalize(value, 1);
    }

    String normalize(String value, int mode) {
        return normalize(value, mode, true);
    }

    String normalize(String value, int mode, boolean trim) {
        return normalize(value, mode, trim, "x");
    }

    String normalize(String value, int mode, boolean trim, String suffix) {
        return normalize(value, mode, trim, suffix, false);
    }

    String normalize(String value, int mode, boolean trim, String suffix, boolean strict) {
        return normalize(value, mode, trim, suffix, strict, 0);
    }

    String normalize(String value, int mode, boolean trim, String suffix, boolean strict, int flags) {
        return value + suffix + flags;
    }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/pay/PaymentClient.java",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_routes_python_classmethod_calls_to_l5() -> None:
    service = L3TreeSitterPreprocessService()
    content = """
class TaskService:
    @classmethod
    def step_one(cls):
        return cls.step_two()

    @classmethod
    def step_two(cls):
        return cls.step_three()

    @classmethod
    def step_three(cls):
        return cls.step_four()

    @classmethod
    def step_four(cls):
        return cls.step_five()

    @classmethod
    def step_five(cls):
        return cls.step_six()

    @classmethod
    def step_six(cls):
        return None
"""

    result = service.preprocess(
        relative_path="src/app/task_service.py",
        content_text=content,
        repo_root="/tmp/repo",
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_routes_python_class_qualified_static_calls_to_l5() -> None:
    service = L3TreeSitterPreprocessService()
    content = """
class TaskService:
    @staticmethod
    def step_one():
        return TaskService.step_two()

    @staticmethod
    def step_two():
        return TaskService.step_three()

    @staticmethod
    def step_three():
        return TaskService.step_four()

    @staticmethod
    def step_four():
        return TaskService.step_five()

    @staticmethod
    def step_five():
        return TaskService.step_six()

    @staticmethod
    def step_six():
        return None
"""

    result = service.preprocess(
        relative_path="src/app/task_service.py",
        content_text=content,
        repo_root="/tmp/repo",
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_java_config_file_on_l3_only() -> None:
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = """
package com.acme.config;

public class PaymentConfig {
    private final String host;
    private final String mid;

    public PaymentConfig(String host, String mid) {
        this.host = host;
        this.mid = mid;
    }

    public String getHost() {
        return host;
    }

    public String getMid() {
        return mid;
    }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/config/PaymentConfig.java",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_java_dto_file_on_l3_only_even_when_call_rich() -> None:
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = """
package com.acme.dto;

public class PaymentDto {
    private String host;
    private String mid;
    private String region;

    String buildHost() {
        return normalize(host);
    }

    String buildMid() {
        return normalize(mid);
    }

    String buildRegion() {
        return normalize(region);
    }

    String normalize(String value) {
        return value == null ? "" : value.trim();
    }

    String decorate(String value) {
        return normalize(value);
    }

    String render() {
        return decorate(buildHost());
    }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/dto/PaymentDto.java",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_package_private_java_methods_without_calls_on_l3_only() -> None:
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = """
package com.acme.client;

class PackagePrivateClient {
    void helperOne() {
    }

    void helperTwo() {
    }

    void helperThree() {
    }

    String helperFour() {
        return "";
    }

    String helperFive() {
        return "";
    }

    String helperSix() {
        return "";
    }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/client/PackagePrivateClient.java",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_one_line_java_method_declarations_on_l3_only() -> None:
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = """
package com.acme.client;

class OneLineClient {
    String helperOne() { return ""; }
    String helperTwo() { return ""; }
    String helperThree() { return ""; }
    String helperFour() { return ""; }
    String helperFive() { return ""; }
    String helperSix() { return ""; }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/client/OneLineClient.java",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_allman_java_method_declarations_on_l3_only() -> None:
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = """
package com.acme.client;

class AllmanClient {
    void helperOne()
    {
    }

    void helperTwo()
    {
    }

    void helperThree()
    {
    }

    String helperFour()
    {
        return "";
    }

    String helperFive()
    {
        return "";
    }

    String helperSix()
    {
        return "";
    }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/client/AllmanClient.java",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_java_interface_signatures_on_l3_only() -> None:
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = """
package com.acme.client;

interface PaymentClient {
    String helperOne();
    String helperTwo();
    String helperThree();
    String helperFour();
    String helperFive();
    String helperSix();
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/client/PaymentClient.java",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_java_constructor_only_client_on_l3_only() -> None:
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = """
package com.acme.client;

class PaymentClient {
    private final String host;
    private final String mid;
    private final String region;

    PaymentClient(String host, String mid, String region) {
        this.host = host;
        this.mid = mid;
        this.region = region;
    }

    String host() {
        return host;
    }

    String mid() {
        return mid;
    }

    String region() {
        return region;
    }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/client/PaymentClient.java",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_java_public_constructor_only_client_on_l3_only() -> None:
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = """
package com.acme.client;

class PaymentClient {
    private final String host;
    private final String mid;
    private final String region;

    public PaymentClient(String host, String mid, String region) {
        this.host = host;
        this.mid = mid;
        this.region = region;
    }

    String host() {
        return host;
    }

    String mid() {
        return mid;
    }

    String region() {
        return region;
    }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/client/PaymentClient.java",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_java_return_type_named_like_keyword_symbols() -> None:
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = """
package com.acme.client;

class Return {}

class PaymentClient {
    Return helperOne() {
        return new Return();
    }

    Return helperTwo() {
        return new Return();
    }

    Return helperThree() {
        return new Return();
    }

    Return helperFour() {
        return new Return();
    }

    Return helperFive() {
        return new Return();
    }

    Return helperSix() {
        return new Return();
    }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/client/PaymentClient.java",
        content_text=content,
    )

    assert len(result.symbols) >= 6


def test_preprocess_keeps_java_super_constructor_only_client_on_l3_only() -> None:
    service = L3TreeSitterPreprocessService()
    content = """
package com.acme.client;

class BaseClient {
    BaseClient(String host) {}
}

class PaymentClient extends BaseClient {
    PaymentClient() {
        super("default");
    }

    PaymentClient(String host) {
        super(host);
    }

    PaymentClient(String host, int mode) {
        super(host + mode);
    }

    PaymentClient(String host, int mode, boolean strict) {
        super(host + mode + strict);
    }

    PaymentClient(String host, int mode, boolean strict, String suffix) {
        super(host + suffix);
    }

    PaymentClient(String host, int mode, boolean strict, String suffix, long flags) {
        super(host + suffix + flags);
    }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/client/PaymentClient.java",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_short_named_java_constructor_chaining_on_l3_only() -> None:
    service = L3TreeSitterPreprocessService()
    content = """
package com.acme.client;

public class Io {
    public Io() {
        this("default");
    }

    public Io(String host) {
        this(host, 0);
    }

    public Io(String host, int mode) {
        this(host, mode, false);
    }

    public Io(String host, int mode, boolean strict) {
        this(host, mode, strict, "x");
    }

    public Io(String host, int mode, boolean strict, String suffix) {
        this(host, mode, strict, suffix, 1L);
    }

    public Io(String host, int mode, boolean strict, String suffix, long flags) {
    }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/client/Io.java",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_routes_java_constructor_chaining_to_l5() -> None:
    service = L3TreeSitterPreprocessService()
    content = """
package com.acme.client;

public class PaymentClient {
    private final String host;

    public PaymentClient() {
        this("default");
    }

    public PaymentClient(String host) {
        this(host, 0);
    }

    public PaymentClient(String host, int mode) {
        this(host, mode, false);
    }

    public PaymentClient(String host, int mode, boolean strict) {
        this(host, mode, strict, "x");
    }

    public PaymentClient(String host, int mode, boolean strict, String suffix) {
        this(host, mode, strict, suffix, 1L);
    }

    public PaymentClient(String host, int mode, boolean strict, String suffix, long flags) {
        this.host = host + suffix + flags;
    }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/client/PaymentClient.java",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5

def test_preprocess_keeps_java_dotted_return_type_declarations_on_l3_only() -> None:
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = """
package com.acme.client;

class PaymentClient {
    java.util.List<String> helperOne() {
        return java.util.List.of();
    }

    Map.Entry<String, String> helperTwo() {
        return null;
    }

    java.util.Optional<String> helperThree() {
        return java.util.Optional.empty();
    }

    java.util.Set<String> helperFour() {
        return java.util.Set.of();
    }

    Map.Entry<String, String> helperFive() {
        return null;
    }

    java.util.List<String> helperSix() {
        return java.util.List.of();
    }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/client/PaymentClient.java",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_wrapped_java_method_signatures_on_l3_only() -> None:
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = """
package com.acme.client;

class PaymentClient {
    String helperOne(
        String value
    ) {
        return value;
    }

    String helperTwo(
        String value
    ) {
        return value;
    }

    String helperThree(
        String value
    ) {
        return value;
    }

    String helperFour(
        String value
    ) {
        return value;
    }

    String helperFive(
        String value
    ) {
        return value;
    }

    String helperSix(
        String value
    ) {
        return value;
    }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/client/PaymentClient.java",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5


def test_preprocess_keeps_java_comment_and_string_call_text_on_l3_only() -> None:
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = """
package com.acme.client;

class PaymentClient {
    String helperOne() {
        return "helperTwo()";
    }

    String helperTwo() {
        return "helperThree()";
    }

    String helperThree() {
        /* helperFour() */
        return "ok";
    }

    String helperFour() {
        return "helperFive()";
    }

    String helperFive() {
        // helperSix()
        return "ok";
    }

    String helperSix() {
        return "ok";
    }
}
"""

    result = service.preprocess(
        relative_path="src/main/java/com/acme/client/PaymentClient.java",
        content_text=content,
    )

    assert result.decision == L3PreprocessDecision.NEEDS_L5
