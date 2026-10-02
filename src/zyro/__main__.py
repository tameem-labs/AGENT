"""Command-line smoke entry point for the ZYRO foundation."""

from zyro.runtime.bootstrap import initialize_runtime


def main() -> None:
    """Initialize the foundation runtime without external services or credentials."""
    runtime = initialize_runtime()
    print(
        "ZYRO foundation runtime initialized "
        f"(environment={runtime.config.environment}, logging={runtime.config.log_level})"
    )


if __name__ == "__main__":
    main()
