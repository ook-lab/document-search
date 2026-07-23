fn main() {
    tauri_build::build();

    // Link tauri_build's generated Windows manifest resource.lib into integration tests
    let out_dir = std::env::var("OUT_DIR").unwrap();
    let res_lib = std::path::Path::new(&out_dir).join("resource.lib");
    if res_lib.exists() {
        println!("cargo:rustc-link-arg-tests={}", res_lib.display());
    }
}
