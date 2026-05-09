"""
NoteForge 环境验证工具
检查所有依赖是否正确安装，并运行快速测试
"""

import sys


def check_python_version():
    """检查 Python 版本"""
    version = sys.version_info
    print(f"\n{'='*50}")
    print(f"  Python 版本: {version.major}.{version.minor}.{version.micro}")
    
    if version.major == 3 and version.minor == 10:
        print("  ✅ 符合要求 (3.10.x)")
        return True
    else:
        print(f"  ⚠️  建议使用 Python 3.10 (当前: {version.major}.{version.minor})")
        return False


def check_package(package_name, import_name=None):
    """检查包是否可导入"""
    try:
        __import__(import_name or package_name)
        print(f"  ✅ {package_name}")
        return True
    except ImportError:
        print(f"  ❌ {package_name} (未安装)")
        return False


def quick_test():
    """快速功能测试"""
    print(f"\n{'='*50}")
    print("  功能测试")
    print(f"{'='*50}")
    
    try:
        from funasr import AutoModel
        
        print("\n[TEST] 加载 Paraformer 模型...")
        model = AutoModel(model="paraformer-zh")
        
        print("[TEST] 创建测试音频 (1秒静音)...")
        import numpy as np
        test_audio = np.zeros(16000, dtype=np.float32)
        
        temp_path = "test_audio.wav"
        import soundfile as sf
        sf.write(temp_path, test_audio, 16000)
        
        print("[TEST] 运行识别...")
        result = model.generate(input=temp_path)
        
        os.remove(temp_path) if os.path.exists(temp_path) else None
        
        print("\n  ✅ NoteForge 测试通过!")
        return True
        
    except Exception as e:
        print(f"\n  ❌ 测试失败: {e}")
        return False


if __name__ == "__main__":
    import os
    
    print("="*60)
    print("  NoteForge 环境验证工具")
    print("="*60)
    
    all_ok = True
    
    all_ok &= check_python_version()
    
    print(f"\n{'='*50}")
    print("  依赖包检查")
    print(f"{'='*50}")
    
    packages = [
        ("torch", "torch"),
        ("torchaudio", "torchaudio"),
        ("funasr", "funasr"),
        ("modelscope", "modelscope"),
        ("onnxruntime", "onnxruntime"),
        ("soundfile", "soundfile"),
        ("numpy", "numpy"),
    ]
    
    for pkg, imp in packages:
        all_ok &= check_package(pkg, imp)
    
    if all_ok:
        all_ok = quick_test()
    
    print(f"\n{'='*60}")
    if all_ok:
        print("  🎉 所有检查通过！NoteForge 已就绪")
        print("")
        print("  下一步:")
        print("  1. python paraformer_transcribe.py ep08")
        print("  2. 或: python paraformer_transcribe.py all")
    else:
        print("  ⚠️  部分检查未通过，请查看上方错误信息")
        print("")
        print("  安装命令:")
        print("  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu")
        print("  pip install funasr modelscope onnxruntime soundfile -i https://pypi.tuna.tsinghua.edu.cn/simple")
    print("="*60)
