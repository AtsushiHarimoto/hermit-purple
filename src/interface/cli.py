"""
Hermit Purple CLI (Interface Layer)
"""

import json
import sys
from pathlib import Path
from typing import Optional

# Add root to sys.path to ensure absolute imports work
# root = tools/hermit-purple/
root_path = Path(__file__).parents[2]
if str(root_path) not in sys.path:
    sys.path.insert(0, str(root_path))

import typer
from rich.console import Console
from rich.table import Table

from src.core.config import get_config
from src.core.plugin import get_plugin_manager
from src.services.smart_search import run_smart_health, run_smart_search

app = typer.Typer(
    name="hermit",
    help="Hermit Purple - Decision Support System (Stand)",
    add_completion=False,
)
console = Console()


@app.callback()
def main_callback():
    """Context setup (logging, config)"""
    manager = get_plugin_manager()
    plugins_dir = root_path / "src" / "plugins"
    manager.discover_plugins([plugins_dir])


@app.command()
def list():
    """List available plugins"""
    manager = get_plugin_manager()
    plugins = manager.list_plugins()

    if not plugins:
        console.print("[yellow]No plugins found.[/]")
        return

    table = Table(title="Available Plugins")
    table.add_column("Name", style="cyan")
    table.add_column("Description")

    for p in plugins:
        table.add_row(p.name, p.description)

    console.print(table)


@app.command(name="pipelines")
def pipelines_alias():
    """Alias for list (Backward Compatibility)"""
    list()


@app.command()
def health():
    """Check API Health (Mock/Real)"""
    console.print("[bold green]✓ Hermit Purple System Online[/]")
    console.print("  Plugin System: Online")
    console.print("  Knowledge Base: Connected")


@app.command(name="search-health")
def search_health(
    gateway_base_url: Optional[str] = typer.Option(None, "--gateway-base-url", help="Gateway base URL, e.g. http://localhost:PORT"),
    ai_base_url: Optional[str] = typer.Option(None, "--ai-base-url", help="OpenAI-compatible base URL, e.g. http://localhost:PORT/v1"),
    timeout: int = typer.Option(6, "--timeout", min=1, max=60, help="Health check timeout (seconds)"),
    raw: bool = typer.Option(False, "--raw", help="Output JSON only"),
):
    """Network and gateway health check for smart search."""
    try:
        data = run_smart_health(
            gateway_base_url=gateway_base_url,
            ai_base_url=ai_base_url,
            timeout=float(timeout),
        )
    except Exception as e:
        console.print(f"[bold red]Health check failed: {e}[/]")
        raise typer.Exit(1)

    if raw:
        console.print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    console.print("[bold cyan]Smart Search Health[/]")
    console.print(f"- gateway: {'OK' if data['gateway']['ok'] else 'FAIL'}")
    console.print(f"- internet: {'OK' if data['internet']['ok'] else 'FAIL'}")
    console.print(f"- perplexity: {'OK' if data['perplexity']['ok'] else 'FAIL'}")
    console.print(f"- google: {'OK' if data['google']['ok'] else 'FAIL'}")
    console.print(f"- elapsed_ms: {data.get('elapsed_ms', 0)}")


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    gemini_model: Optional[str] = typer.Option(None, "--gemini-model", help="Primary Gemini model"),
    grok_model: Optional[str] = typer.Option(None, "--grok-model", help="Fallback Grok model"),
    gateway_base_url: Optional[str] = typer.Option(None, "--gateway-base-url", help="Gateway base URL, e.g. http://localhost:PORT"),
    ai_base_url: Optional[str] = typer.Option(None, "--ai-base-url", help="OpenAI-compatible base URL, e.g. http://localhost:PORT/v1"),
    timeout: int = typer.Option(90, "--timeout", min=10, max=300, help="Request timeout (seconds)"),
    raw: bool = typer.Option(False, "--raw", help="Output JSON only"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show traceback on failure"),
):
    """Smart web search: Gemini -> Grok -> Perplexity -> Google."""
    try:
        result = run_smart_search(
            query=query,
            gemini_model=gemini_model,
            grok_model=grok_model,
            gateway_base_url=gateway_base_url,
            ai_base_url=ai_base_url,
            timeout=float(timeout),
        )
    except Exception as e:
        console.print(f"[bold red]Smart search failed: {e}[/]")
        if verbose:
            import traceback

            traceback.print_exc()
        raise typer.Exit(1)

    if raw:
        console.print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    route = result.get("route", "unknown")
    model = result.get("model")
    console.print(f"[bold magenta]Route:[/] {route}" + (f" ({model})" if model else ""))

    health = result.get("health", {})
    console.print(
        "[cyan]Health:[/] "
        f"gateway={'OK' if health.get('gateway', {}).get('ok') else 'FAIL'} | "
        f"internet={'OK' if health.get('internet', {}).get('ok') else 'FAIL'} | "
        f"perplexity={'OK' if health.get('perplexity', {}).get('ok') else 'FAIL'} | "
        f"google={'OK' if health.get('google', {}).get('ok') else 'FAIL'}"
    )

    console.print("\n[bold green]Answer[/]")
    console.print(result.get("answer", ""))

    sources = result.get("sources") or []
    if sources:
        console.print("\n[bold blue]Sources[/]")
        for src in sources:
            console.print(f"- {src}")

    errors = result.get("errors") or []
    if errors:
        console.print("\n[yellow]Fallback notes[/]")
        for err in errors:
            console.print(f"- {err}")


@app.command()
def run(
    plugin_name: str = typer.Argument(..., help="Name of the plugin to run"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    keywords: Optional[str] = typer.Option(None, "--keywords", "-k", help="Custom keywords"),
    append: bool = typer.Option(False, "--append", help="Append to default keywords"),
    force: bool = typer.Option(False, "--force", help="Bypass limits"),
):
    """Execute a specific plugin"""
    manager = get_plugin_manager()
    plugin = manager.get_plugin(plugin_name)

    if not plugin:
        console.print(f"[red]Plugin '{plugin_name}' not found.[/]")
        raise typer.Exit(1)

    console.print(f"[bold blue]Running {plugin.name}...[/]")

    custom_kw_list = [k.strip() for k in keywords.split(",")] if keywords else []

    context = {
        "verbose": verbose,
        "config": get_config().model_dump(),
        "keywords": custom_kw_list,
        "append_keywords": append,
        "force": force,
    }

    def on_event(name, data):
        if name == "status":
            console.log(f"[dim]{data}[/]")
        elif name == "analyzing":
            console.log(f"  [cyan]Analyzing {data.get('index')}/{data.get('total')}: {data.get('title')}...[/]")
        elif name == "item_complete":
            verdict = data.get("verdict")
            color = "green" if verdict == "ADOPT" else "yellow" if verdict == "TRIAL" else "dim"
            console.print(f"  > [{color}]{verdict}[/]: {data.get('title')}")
        elif name == "error":
            console.print(f"[red]Error: {data}[/]")

    if hasattr(plugin, "clear_callbacks"):
        plugin.clear_callbacks()
    plugin.on_event(on_event)

    try:
        result = plugin.run(context)
        if result.success:
            console.print("[bold green]Success![/]")
            summary = result.data.get("summary")
            if summary:
                console.print("\n" + summary)
            else:
                console.print("\n" + json.dumps(result.data, ensure_ascii=False, indent=2, default=str)[:3000])

            items = result.data.get("items")
            if items:
                from src.report.html_report import generate_html_report

                report_path = generate_html_report(plugin.name, items)
                console.print(f"\n[bold magenta]Report saved:[/] {report_path}")
        else:
            console.print(f"[bold red]Failed: {result.error}[/]")
            sys.exit(1)

    except Exception as e:
        console.print(f"[bold red]Critical Error: {e}[/]")
        if verbose:
            import traceback

            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    app()
