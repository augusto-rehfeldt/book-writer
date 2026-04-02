"""Project-management command line interface."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime

from .core.project_manager import ProjectManager, STEP_SEQUENCE
from .utils.glossary_manager import GlossaryManager


def list_projects() -> None:
    """List all book projects in the current directory."""
    projects = []
    for item in os.listdir("."):
        if os.path.isdir(item) and os.path.exists(
            os.path.join(item, "project_data.json")
        ):
            try:
                pm = ProjectManager(item)
                pm.load_project()
                summary = pm.get_project_summary()
                projects.append(
                    {
                        "directory": item,
                        "steps_completed": len(summary.get("healthy_steps_completed", summary["steps_completed"])),
                        "total_chapters": summary["total_chapters"],
                        "total_words": summary["total_words"],
                        "state_status": summary.get("state_status", "unknown"),
                    }
                )
            except Exception as e:
                projects.append({"directory": item, "error": str(e)})

    if not projects:
        print("No book projects found in current directory.")
        return

    print("Found Book Projects:")
    print("-" * 50)
    for project in projects:
        if "error" in project:
            print(f"📁 {project['directory']} - ERROR: {project['error']}")
        else:
            print(f"📁 {project['directory']}")
            print(f"   Steps completed: {project['steps_completed']}/{len(STEP_SEQUENCE)}")
            print(f"   Chapters: {project['total_chapters']}")
            print(f"   Words: {project['total_words']:,}")
            if project.get("state_status") == "broken":
                print("   State: broken - rewrite from the latest correct step")
            print()


def show_project_status(project_dir: str) -> None:
    """Show detailed status of a specific project."""
    if not os.path.exists(project_dir):
        print(f"Project directory '{project_dir}' not found.")
        return

    try:
        pm = ProjectManager(project_dir)
        if not pm.load_project():
            print(f"No project data found in '{project_dir}'.")
            return

        summary = pm.get_project_summary()

        print(f"Project Status: {project_dir}")
        print("=" * 50)
        print(f"Steps completed: {len(summary['steps_completed'])}")
        print(f"Total chapters: {summary['total_chapters']}")
        print(f"Total words: {summary['total_words']:,}")
        print(f"Estimated pages: {summary['estimated_pages']}")
        print(f"State: {summary.get('state_status', 'unknown')}")
        if summary.get("latest_valid_step"):
            print(f"Latest correct step: {summary['latest_valid_step']}")
        if summary.get("recommended_restart_step"):
            print(f"Recommended restart step: {summary['recommended_restart_step']}")
        if summary.get("broken_steps"):
            print(f"Broken steps: {', '.join(summary['broken_steps'])}")

        glossary_file = os.path.join(project_dir, "glossary.json")
        if os.path.exists(glossary_file):
            gm = GlossaryManager(project_dir)
            stats = gm.get_glossary_stats()
            print("\nGlossary Statistics:")
            print(f"  Characters: {stats['characters']}")
            print(f"  Locations: {stats['locations']}")
            print(f"  Concepts: {stats['concepts']}")
            print(f"  Timeline events: {stats['timeline_events']}")
            print(f"  Relationships: {stats['relationships']}")

        if "_metadata" in pm.book_data:
            last_updated = pm.book_data["_metadata"].get("last_updated", "Unknown")
            print(f"\nLast updated: {last_updated}")

        runtime_state = pm.book_data.get("_runtime", {})
        if runtime_state.get("status") == "paused_for_budget":
            print("\nBudget pause:")
            print(f"  {runtime_state.get('message', 'Daily token budget was exceeded.')}")
            if runtime_state.get("bucket"):
                print(f"  Bucket: {runtime_state.get('bucket')}")
            if runtime_state.get("tokens_used") and runtime_state.get("token_limit"):
                print(
                    f"  Usage: {runtime_state.get('tokens_used'):,}/{runtime_state.get('token_limit'):,} tokens"
                )

    except Exception as e:
        print(f"Error reading project: {e}")


def backup_project(project_dir: str, backup_dir: str | None = None) -> None:
    """Create a backup of a project."""
    if not os.path.exists(project_dir):
        print(f"Project directory '{project_dir}' not found.")
        return

    if backup_dir is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = f"{project_dir}_backup_{timestamp}"

    try:
        import shutil

        shutil.copytree(project_dir, backup_dir)
        print(f"Project backed up to: {backup_dir}")
    except Exception as e:
        print(f"Backup failed: {e}")


def export_glossary(project_dir: str, format: str = "txt") -> None:
    """Export glossary in different formats."""
    if not os.path.exists(project_dir):
        print(f"Project directory '{project_dir}' not found.")
        return

    try:
        gm = GlossaryManager(project_dir)

        if format.lower() == "json":
            output_file = f"{project_dir}_glossary.json"
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(gm.glossary, f, indent=4, ensure_ascii=False)
        else:
            output_file = f"{project_dir}_glossary.txt"
            content = gm._format_glossary_content()
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(f"Glossary Export: {project_dir}\n")
                f.write("=" * 50 + "\n\n")
                f.write(content)

        print(f"Glossary exported to: {output_file}")

    except Exception as e:
        print(f"Export failed: {e}")


def clean_project(project_dir: str) -> None:
    """Clean up temporary files from a project."""
    if not os.path.exists(project_dir):
        print(f"Project directory '{project_dir}' not found.")
        return

    cleaned_files = []
    for file in os.listdir(project_dir):
        if file.startswith("checkpoint_") and file.endswith(".json"):
            file_path = os.path.join(project_dir, file)
            os.remove(file_path)
            cleaned_files.append(file)

    if cleaned_files:
        print(f"Cleaned up {len(cleaned_files)} temporary files:")
        for file in cleaned_files:
            print(f"  - {file}")
    else:
        print("No temporary files found to clean.")


def repair_project(project_dir: str, from_step: str | None = None) -> None:
    """Clear a project from the given step onward so it can be rebuilt cleanly."""
    if not os.path.exists(project_dir):
        print(f"Project directory '{project_dir}' not found.")
        return

    try:
        pm = ProjectManager(project_dir)
        if not pm.load_project():
            print(f"No project data found in '{project_dir}'.")
            return

        plan = pm.get_recovery_plan()
        step_to_reset = from_step or plan.get("restart_step")

        if not step_to_reset:
            print("Project state is already healthy or fully complete. No repair needed.")
            return

        pm.reset_steps_from(step_to_reset)
        pm.save_project()

        print(f"Repaired project by clearing from: {step_to_reset}")
        if plan.get("latest_valid_step_label"):
            print(f"Latest correct step: {plan['latest_valid_step_label']}")
        if plan.get("restart_step_label"):
            print(f"Suggested rewrite point: {plan['restart_step_label']}")

    except Exception as e:
        print(f"Repair failed: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Book Creator Project Manager")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    subparsers.add_parser("list", help="List projects in the current directory")

    status_parser = subparsers.add_parser("status", help="Show project status")
    status_parser.add_argument("project_dir", help="Project directory to check")

    backup_parser = subparsers.add_parser("backup", help="Backup a project")
    backup_parser.add_argument("project_dir", help="Project directory to backup")
    backup_parser.add_argument("--output", help="Backup directory name (optional)")

    export_parser = subparsers.add_parser(
        "export-glossary", help="Export project glossary"
    )
    export_parser.add_argument("project_dir", help="Project directory")
    export_parser.add_argument(
        "--format", choices=["txt", "json"], default="txt", help="Export format"
    )

    clean_parser = subparsers.add_parser("clean", help="Clean temporary files")
    clean_parser.add_argument("project_dir", help="Project directory to clean")

    repair_parser = subparsers.add_parser("repair", help="Clear broken downstream project state")
    repair_parser.add_argument("project_dir", help="Project directory to repair")
    repair_parser.add_argument(
        "--from-step",
        choices=["init", "structure", "written", "reviewed"],
        help="Optional step name to clear from instead of the detected restart point",
    )

    args = parser.parse_args()

    if args.command == "list":
        list_projects()
    elif args.command == "status":
        show_project_status(args.project_dir)
    elif args.command == "backup":
        backup_project(args.project_dir, args.output)
    elif args.command == "export-glossary":
        export_glossary(args.project_dir, args.format)
    elif args.command == "clean":
        clean_project(args.project_dir)
    elif args.command == "repair":
        repair_project(args.project_dir, args.from_step)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
