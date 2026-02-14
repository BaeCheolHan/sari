use std::env;
use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone)]
struct Opts {
    root: PathBuf,
    max_depth: usize,
    follow_symlinks: bool,
    exclude_dirs: Vec<String>,
}

fn parse_args() -> Result<Opts, String> {
    let mut args = env::args().skip(1);
    let mut root: Option<PathBuf> = None;
    let mut max_depth: usize = 64;
    let mut follow_symlinks = false;
    let mut exclude_dirs: Vec<String> = Vec::new();

    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--root" => {
                let v = args.next().ok_or("missing value for --root")?;
                root = Some(PathBuf::from(v));
            }
            "--max-depth" => {
                let v = args.next().ok_or("missing value for --max-depth")?;
                max_depth = v.parse::<usize>().map_err(|_| "invalid max depth")?;
            }
            "--follow-symlinks" => {
                follow_symlinks = true;
            }
            "--exclude-dir" => {
                let v = args.next().ok_or("missing value for --exclude-dir")?;
                if !v.trim().is_empty() {
                    exclude_dirs.push(v.trim().to_string());
                }
            }
            _ => return Err(format!("unknown argument: {}", arg)),
        }
    }

    let root = root.ok_or("--root is required")?;
    Ok(Opts {
        root,
        max_depth,
        follow_symlinks,
        exclude_dirs,
    })
}

fn normalize_pattern_token(pat: &str) -> String {
    let mut s = pat.trim().replace('\\', "/");
    if s.ends_with("/**") {
        s.truncate(s.len() - 3);
    }
    while s.ends_with('/') {
        s.pop();
    }
    s
}

fn should_exclude_dir(dir_name: &str, rel_posix: &str, patterns: &[String]) -> bool {
    for pat in patterns {
        let token = normalize_pattern_token(pat);
        if token.is_empty() {
            continue;
        }
        if !token.contains('*') && !token.contains('?') && !token.contains('[') {
            if dir_name == token || rel_posix == token || rel_posix.starts_with(&(token.clone() + "/")) {
                return true;
            }
        }
    }
    false
}

fn scan_dir(
    root: &Path,
    current: &Path,
    depth: usize,
    opts: &Opts,
    out: &mut dyn Write,
) -> io::Result<()> {
    if depth > opts.max_depth {
        return Ok(());
    }

    let entries = match fs::read_dir(current) {
        Ok(v) => v,
        Err(_) => return Ok(()),
    };

    for entry_res in entries {
        let entry = match entry_res {
            Ok(v) => v,
            Err(_) => continue,
        };
        let path = entry.path();

        let ft = match entry.file_type() {
            Ok(v) => v,
            Err(_) => continue,
        };

        let rel = match path.strip_prefix(root) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let rel_posix = rel.to_string_lossy().replace('\\', "/");

        if ft.is_dir() {
            let dir_name = entry.file_name().to_string_lossy().to_string();
            if should_exclude_dir(&dir_name, &rel_posix, &opts.exclude_dirs) {
                continue;
            }
            if !opts.follow_symlinks && ft.is_symlink() {
                continue;
            }
            let _ = scan_dir(root, &path, depth + 1, opts, out);
            continue;
        }

        if ft.is_file() {
            let meta = match fs::metadata(&path) {
                Ok(m) => m,
                Err(_) => continue,
            };
            let mtime = meta
                .modified()
                .ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_secs() as i64)
                .unwrap_or(0);
            let size = meta.len();
            // Keep raw entry path to avoid expensive per-file canonicalize.
            writeln!(out, "{}\t{}\t{}", path.display(), mtime, size)?;
        }
    }
    Ok(())
}

fn main() {
    let opts = match parse_args() {
        Ok(v) => v,
        Err(e) => {
            eprintln!("{}", e);
            std::process::exit(2);
        }
    };

    let mut out = io::BufWriter::new(io::stdout());
    if let Err(e) = scan_dir(&opts.root, &opts.root, 0, &opts, &mut out) {
        eprintln!("scan failed: {}", e);
        std::process::exit(1);
    }
}
