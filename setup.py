# setup.py
#!/usr/bin/env python3
import shutil
from pathlib import Path

def main():
    print("Setting up local configuration files...")
    config_dir = Path("ai_book_creator/config")
    
    # Create local copies of all JSON configs
    if config_dir.exists():
        for src in config_dir.glob("ai_config_*.json"):
            if ".local." in src.name:
                continue
            
            dst = src.with_name(src.stem + ".local.json")
            if not dst.exists():
                shutil.copy2(src, dst)
                print(f"Created {dst} (Edit this to change your daily token limits safely)")
            else:
                print(f"Already exists: {dst}")

    # Copy .env.example to .env if applicable
    env_src = Path(".env.example")
    env_dst = Path(".env")
    if env_src.exists() and not env_dst.exists():
        shutil.copy2(env_src, env_dst)
        print("Created .env from .env.example")
        
    print("\nSetup complete! You can now edit the *.local.json files in ai_book_creator/config/ to adjust max daily set tokens. These files are ignored by git.")

if __name__ == "__main__":
    main()