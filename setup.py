# setup.py
#!/usr/bin/env python3
import shutil
from pathlib import Path

def main():
    print("Setting up local configuration files...")
    config_dir = Path("ai_book_creator/config")
    created_any = False

    # Create local copies of all JSON configs
    if config_dir.exists():
        for src in config_dir.glob("ai_config_*.json"):
            if ".local." in src.name:
                continue

            dst = src.with_name(src.stem + ".local.json")
            if not dst.exists():
                shutil.copy2(src, dst)
                print(f"Created {dst} (Edit this to change your daily token limits safely)")
                created_any = True
            else:
                print(f"Already exists: {dst}")

    if not created_any:
        local_configs = sorted(config_dir.glob("ai_config_*.local.json")) if config_dir.exists() else []
        if local_configs:
            print("Local config files already exist:")
            for path in local_configs:
                print(f"  - {path}")
        else:
            print("No config templates were found. If you add base ai_config_*.json templates, rerun setup to generate local copies.")

    # Copy .env.example to .env if applicable
    env_src = Path(".env.example")
    env_dst = Path(".env")
    if env_src.exists() and not env_dst.exists():
        shutil.copy2(env_src, env_dst)
        print("Created .env from .env.example")
        
    print("\nSetup complete! Edit the *.local.json files in ai_book_creator/config/ to adjust limits and models.")

if __name__ == "__main__":
    main()