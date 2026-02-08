import pytest
import json
from sari.core.parsers.ast_engine import ASTEngine

def test_spring_data_jpa_integrity():
    """
    Verify that Sari understands JPA Entities and Repositories.
    """
    engine = ASTEngine()
    code = (
        "@Entity\n"
        "@Table(name = \"users\")\n"
        "public class User {\n"
        "    @Id private Long id;\n"
        "}\n"
        "\n"
        "public interface UserRepository extends JpaRepository<User, Long> {}\n"
    )
    # --- FIX: UNPACK PROPERLY ---
    symbols, _ = engine.extract_symbols("User.java", "java", code)
    
    # Check Entity
    user_cls = next(s for s in symbols if s[3] == "User")
    user_meta = json.loads(user_cls[9])
    assert "Entity" in user_meta["annotations"]
    
    # Check Repository
    repo_iface = next(s for s in symbols if s[3] == "UserRepository")
    repo_meta = json.loads(repo_iface[9])
    assert repo_meta["framework_role"] == "Repository"
    assert any("JpaRepository" in h for h in repo_meta["extends"])
    
    print(f"\nDEBUG: JPA SUCCESS. Repository found: {repo_iface[1]}")

def test_spring_data_redis_and_caching():
    """
    Verify that Redis and Caching markers are captured.
    """
    engine = ASTEngine()
    code = (
        "@RedisHash(\"user_cache\")\n"
        "public class UserSession {}\n"
        "\n"
        "@Service\n"
        "public class UserService {\n"
        "    @Cacheable(\"users\")\n"
        "    public User findById(Long id) { return null; }\n"
        "}\n"
    )
    symbols, _ = engine.extract_symbols("Redis.java", "java", code)
    
    session_cls = next(s for s in symbols if s[3] == "UserSession")
    assert "RedisHash" in json.loads(session_cls[9])["annotations"]
    
    cache_method = next(s for s in symbols if s[3] == "findById")
    assert "Cacheable" in json.loads(cache_method[9])["annotations"]
    print(f"DEBUG: Redis/Caching SUCCESS. Found: {session_cls[3]}, {cache_method[1]}")

def test_spring_webflux_reactive_truth():
    """
    Verify that WebFlux reactive return types (Mono/Flux) are detected.
    """
    engine = ASTEngine()
    code = (
        "@RestController\n"
        "public class FluxController {\n"
        "    public Mono<String> getHello() { return Mono.just(\"hi\"); }\n"
        "    public Flux<User> listUsers() { return Flux.empty(); }\n"
        "}\n"
    )
    symbols, _ = engine.extract_symbols("Flux.java", "java", code)
    
    hello_fn = next(s for s in symbols if s[3] == "getHello")
    meta = json.loads(hello_fn[9])
    assert meta["reactive"] is True
    assert "Mono" in meta["return_type"]
    
    print(f"DEBUG: WebFlux SUCCESS. Reactive method: {hello_fn[1]} ({meta['return_type']})")