use glob::glob;
use std::collections::HashMap;
use std::fs;
use syn::{parse_file, visit::Visit, ExprMacro, ItemMacro, Token};

#[derive(Debug)]
struct EventDefinition {
    name: String,
    python_signature: String,
    source_file: String,
}

struct EventVisitor {
    events: Vec<EventDefinition>,
}

impl<'ast> Visit<'ast> for EventVisitor {
    fn visit_expr_macro(&mut self, node: &'ast ExprMacro) {
        let macro_name = node
            .mac
            .path
            .segments
            .last()
            .map(|seg| seg.ident.to_string())
            .unwrap_or_default();

        if macro_name == "define_event" {
            if let Some(event) = self.parse_define_event(&node.mac.tokens) {
                self.events.push(event);
            }
        }

        // Continue visiting children
        syn::visit::visit_expr_macro(self, node);
    }

    fn visit_item_macro(&mut self, node: &'ast ItemMacro) {
        let macro_name = node
            .mac
            .path
            .segments
            .last()
            .map(|seg| seg.ident.to_string())
            .unwrap_or_default();

        if macro_name == "define_event" {
            if let Some(event) = self.parse_define_event(&node.mac.tokens) {
                self.events.push(event);
            }
        }

        // Continue visiting children
        syn::visit::visit_item_macro(self, node);
    }
}

impl EventVisitor {
    fn new() -> Self {
        Self { events: Vec::new() }
    }

    fn parse_define_event(&self, tokens: &proc_macro2::TokenStream) -> Option<EventDefinition> {
        use syn::parse::{Parse, ParseStream};
        use syn::{parenthesized, Ident, LitStr, Token};

        struct DefineEventArgs {
            name: Ident,
            _comma1: Token![,],
            _fn_token: Token![fn],
            _paren: syn::token::Paren,
            _params: proc_macro2::TokenStream, // We'll parse this as a raw token stream
            _comma2: Token![,],
            python_sig: LitStr,
        }

        impl Parse for DefineEventArgs {
            fn parse(input: ParseStream) -> syn::Result<Self> {
                let name = input.parse()?;
                let _comma1 = input.parse()?;
                let _fn_token = input.parse()?;

                let content;
                let _paren = parenthesized!(content in input);
                let _params = content.parse()?; // Get the raw parameter tokens

                let _comma2 = input.parse()?;
                let python_sig = input.parse()?;

                Ok(DefineEventArgs {
                    name,
                    _comma1,
                    _fn_token,
                    _paren,
                    _params,
                    _comma2,
                    python_sig,
                })
            }
        }

        // Try to parse the tokens as a define_event! macro call
        if let Ok(args) = syn::parse2::<DefineEventArgs>(tokens.clone()) {
            let name = args.name.to_string();
            let python_sig = args.python_sig.value();

            return Some(EventDefinition {
                name,
                python_signature: python_sig,
                source_file: String::new(), // Will be set by parse_events_from_files
            });
        }

        None
    }
}

fn find_rust_files() -> Vec<std::path::PathBuf> {
    let mut files = Vec::new();

    for entry in glob("**/*.rs").expect("Failed to read glob pattern") {
        match entry {
            Ok(path) => {
                // Skip target directory
                if !path.to_string_lossy().contains("target/") {
                    files.push(path);
                }
            }
            Err(e) => eprintln!("Error reading path: {:?}", e),
        }
    }

    files
}

fn parse_events_from_files(files: &[std::path::PathBuf]) -> Vec<EventDefinition> {
    let mut all_events = Vec::new();

    for file_path in files {
        if let Ok(content) = fs::read_to_string(file_path) {
            if let Ok(syntax_tree) = parse_file(&content) {
                let mut visitor = EventVisitor::new();
                visitor.visit_file(&syntax_tree);

                // Add source file to each event
                let source_file = file_path.to_string_lossy().to_string();
                for mut event in visitor.events {
                    event.source_file = source_file.clone();
                    all_events.push(event);
                }
            }
        }
    }

    all_events
}

fn check_duplicate_events(events: &[EventDefinition]) {
    let mut event_map: HashMap<&str, Vec<&str>> = HashMap::new();

    // Build map of event names to their source files
    for event in events {
        event_map
            .entry(&event.name)
            .or_default()
            .push(&event.source_file);
    }

    // Check for duplicates
    let mut has_duplicates = false;
    for (name, files) in event_map.iter() {
        if files.len() > 1 {
            has_duplicates = true;
            eprintln!("Error: Duplicate event name '{}' found in:", name);
            for file in files {
                eprintln!("  - {}", file);
            }
        }
    }

    if has_duplicates {
        panic!("Event names must be unique across the entire codebase");
    }
}

fn generate_python_type_from_signature(signature: &str) -> String {
    if signature.is_empty() {
        "[]".to_string()
    } else {
        // Use the signature content directly in Event[[...]]
        format!("[{}]", signature)
    }
}

fn generate_events_pyi(events: &[EventDefinition]) -> String {
    let header = r#"# This file is automatically generated during compilation of src/native/
# Do not edit manually - changes will be overwritten
# Generated by build.rs from define_event! macro calls
# Any modifications should be committed to the repository after regeneration
# mypy: disable-error-code="name-defined"

from typing import Callable, ParamSpec, Protocol

P = ParamSpec('P')

class ListenerHandle:
    """Handle for registered event listeners"""
    id: int
    event_id: str
    def __str__(self) -> str: ...
    def __repr__(self) -> str: ...

class Event(Protocol[P]):
    """Generic event protocol with strongly typed dispatch and listeners"""
    def dispatch(self, *args: P.args) -> None:
        """Fire the event to all registered listeners"""
        ...

    def on(self, callback: Callable[P, None]) -> ListenerHandle:
        """Register a listener callback for this event"""
        ...

    def remove(self, handle: ListenerHandle) -> bool:
        """Remove a specific listener by handle"""
        ...

    def has_listeners(self) -> bool:
        """Check if any listeners are registered for this event"""
        ...

# Event instances
"#;

    let mut result = header.to_string();

    // Generate event definitions
    for event in events {
        let python_type = generate_python_type_from_signature(&event.python_signature);

        // Add noqa comment for ruff if signature contains forward references (quoted strings)
        // (mypy is handled by file-level disable-error-code in header)
        let line = if event.python_signature.contains('"') {
            format!("\n{}: Event[{}]  # noqa: F821", event.name, python_type)
        } else {
            format!("\n{}: Event[{}]", event.name, python_type)
        };

        result.push_str(&line);
    }

    result.push('\n');
    result
}

fn main() {
    //NOTE(@dmehala): PyO3 doesn't link to `libpython` on MacOS.
    // This set the correct linker arguments for the platform.
    // Source: <https://pyo3.rs/main/building-and-distribution.html#macos>
    if cfg!(target_os = "macos") {
        pyo3_build_config::add_extension_module_link_args();
    }

    // Generate events.pyi from macro calls
    println!("cargo:rerun-if-changed=./");

    let rust_files = find_rust_files();
    let events = parse_events_from_files(&rust_files);

    if !events.is_empty() {
        // Check for duplicate event names before generating .pyi
        check_duplicate_events(&events);

        let pyi_content = generate_events_pyi(&events);

        // Use CARGO_MANIFEST_DIR to get consistent path
        let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").unwrap_or_else(|_| ".".to_string());
        let output_path = format!("{}/../../ddtrace/internal/events.pyi", manifest_dir);

        if let Err(e) = fs::write(output_path, pyi_content) {
            println!("cargo::warning=Could not write events.pyi: {}", e);
        }
    }
}
