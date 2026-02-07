"""신규 언어 및 프레임워크 지원 테스트"""
import pytest
from sari.core.parsers.ast_engine import ASTEngine


class TestKotlinSpring:
    """Kotlin/Spring 지원 테스트"""
    
    def test_kotlin_spring_controller(self):
        """Kotlin Spring Controller with annotations"""
        engine = ASTEngine()
        code = '''
@RestController
@RequestMapping("/api/users")
class UserController(private val userService: UserService) {
    @GetMapping("/{id}")
    fun getUser(@PathVariable id: Long): User {
        return userService.findById(id)
    }
    
    @PostMapping
    suspend fun createUser(@RequestBody user: UserRequest): User {
        return userService.create(user)
    }
}
'''
        symbols, _ = engine.extract_symbols("UserController.kt", "kotlin", code)
        names = [s[1] for s in symbols]
        
        assert "UserController" in names
        assert "getUser" in names or len([s for s in symbols if s[2] == "function"]) > 0
    
    def test_kotlin_data_class(self):
        """Kotlin data class"""
        engine = ASTEngine()
        code = '''
data class User(
    val id: Long,
    val name: String,
    val email: String
)

sealed class Result<out T> {
    data class Success<T>(val data: T) : Result<T>()
    data class Error(val message: String) : Result<Nothing>()
}

object UserRepository {
    private val users = mutableListOf<User>()
    
    fun findAll(): List<User> = users
}
'''
        symbols, _ = engine.extract_symbols("Models.kt", "kotlin", code)
        names = [s[1] for s in symbols]
        
        # data class
        assert "User" in names
        # object
        assert "UserRepository" in names


class TestPHPLaravel:
    """PHP/Laravel 지원 테스트"""
    
    def test_laravel_controller(self):
        """Laravel Controller"""
        engine = ASTEngine()
        code = '''<?php
namespace App\\Http\\Controllers;

use App\\Models\\User;
use Illuminate\\Http\\Request;

class UserController extends Controller {
    public function index() {
        return User::all();
    }
    
    public function store(Request $request) {
        return User::create($request->validated());
    }
    
    public function show(User $user) {
        return $user;
    }
}
'''
        symbols, _ = engine.extract_symbols("UserController.php", "php", code)
        names = [s[1] for s in symbols]
        
        assert "UserController" in names
        assert "index" in names or "store" in names
    
    def test_laravel_model(self):
        """Laravel Eloquent Model"""
        engine = ASTEngine()
        code = '''<?php
namespace App\\Models;

class User extends Model {
    protected $fillable = ['name', 'email', 'password'];
    
    public function posts() {
        return $this->hasMany(Post::class);
    }
    
    public function profile() {
        return $this->hasOne(Profile::class);
    }
}
'''
        symbols, _ = engine.extract_symbols("User.php", "php", code)
        names = [s[1] for s in symbols]
        kinds = [s[2] for s in symbols]
        
        assert "User" in names
        assert "class" in kinds


class TestRubyRails:
    """Ruby/Rails 지원 테스트"""
    
    def test_rails_model(self):
        """Rails ActiveRecord Model"""
        engine = ASTEngine()
        code = '''
class User < ApplicationRecord
  has_many :posts, dependent: :destroy
  has_one :profile
  belongs_to :organization, optional: true
  
  validates :email, presence: true, uniqueness: true
  validates :name, length: { minimum: 2 }
  
  scope :active, -> { where(active: true) }
  
  def full_name
    "#{first_name} #{last_name}"
  end
end
'''
        symbols, _ = engine.extract_symbols("user.rb", "ruby", code)
        names = [s[1] for s in symbols]
        
        assert "User" in names
        assert "full_name" in names
    
    def test_rails_controller(self):
        """Rails Controller"""
        engine = ASTEngine()
        code = '''
class UsersController < ApplicationController
  before_action :authenticate_user!
  before_action :set_user, only: [:show, :edit, :update, :destroy]
  
  def index
    @users = User.all
  end
  
  def show
  end
  
  def create
    @user = User.new(user_params)
    if @user.save
      redirect_to @user
    else
      render :new
    end
  end
  
  private
  
  def user_params
    params.require(:user).permit(:name, :email)
  end
end
'''
        symbols, _ = engine.extract_symbols("users_controller.rb", "ruby", code)
        names = [s[1] for s in symbols]
        
        assert "UsersController" in names
        assert "index" in names or "create" in names


class TestPythonFastAPI:
    """Python FastAPI/Flask 지원 테스트"""
    
    def test_fastapi_routes(self):
        """FastAPI route handlers"""
        engine = ASTEngine()
        code = '''
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel

app = FastAPI()

class UserCreate(BaseModel):
    name: str
    email: str

@app.get("/users/{user_id}")
async def get_user(user_id: int):
    return {"id": user_id}

@app.post("/users")
async def create_user(user: UserCreate):
    return user

@app.delete("/users/{user_id}")
async def delete_user(user_id: int):
    return {"deleted": user_id}
'''
        symbols, _ = engine.extract_symbols("main.py", "python", code)
        names = [s[1] for s in symbols]
        
        # Pydantic model
        assert "UserCreate" in names
        # Route handlers
        assert "get_user" in names or "create_user" in names
    
    def test_flask_routes(self):
        """Flask route handlers"""
        engine = ASTEngine()
        code = '''
from flask import Flask, jsonify, request

app = Flask(__name__)

@app.get("/items")
def list_items():
    return jsonify([])

@app.post("/items")
def create_item():
    data = request.json
    return jsonify(data)

@blueprint.get("/categories")
def list_categories():
    return jsonify([])
'''
        symbols, _ = engine.extract_symbols("routes.py", "python", code)
        names = [s[1] for s in symbols]
        
        # 최소한 하나의 라우트 핸들러가 감지되어야 함
        assert "list_items" in names or "create_item" in names


class TestYAMLKubernetes:
    """YAML/Kubernetes 지원 테스트"""
    
    def test_k8s_deployment(self):
        """Kubernetes Deployment"""
        engine = ASTEngine()
        code = '''
apiVersion: apps/v1
kind: Deployment
metadata:
  name: user-service
  namespace: production
spec:
  replicas: 3
  selector:
    matchLabels:
      app: user-service
  template:
    spec:
      containers:
        - name: app
          image: user-service:latest
          ports:
            - containerPort: 8080
'''
        symbols, _ = engine.extract_symbols("deployment.yaml", "yaml", code)
        names = [s[1] for s in symbols]
        
        # K8s 리소스 감지
        assert any("Deployment" in n for n in names) or any("user-service" in n for n in names)
    
    def test_k8s_service(self):
        """Kubernetes Service"""
        engine = ASTEngine()
        code = '''
apiVersion: v1
kind: Service
metadata:
  name: user-svc
spec:
  selector:
    app: user-service
  ports:
    - port: 80
      targetPort: 8080
  type: ClusterIP
'''
        symbols, _ = engine.extract_symbols("service.yml", "yaml", code)
        names = [s[1] for s in symbols]
        
        assert any("Service" in n for n in names) or any("user-svc" in n for n in names)
