{
  description = "Python client library for duckwm";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      py = pkgs.python3;

      duckwm = py.pkgs.buildPythonPackage {
        pname = "duckwm";
        version = "0.1.0";
        pyproject = true;
        src = ./.;
        build-system = [ py.pkgs.hatchling ];
        dependencies = [ py.pkgs.protobuf ];
        pythonImportsCheck = [ "duckwm" ];
      };
    in
    {
      packages.${system}.default = duckwm;

      devShells.${system}.default = pkgs.mkShell {
        packages = [
          (py.withPackages (p: [
            p.protobuf
            p.pytest
            p.mypy
          ]))
          pkgs.protobuf # protoc
          py.pkgs.mypy-protobuf # protoc-gen-mypy, for the .pyi stubs
        ];
        shellHook = ''
          export DUCKWM_SRC=/home/duck/dev/duckwm
          export PYTHONPATH=$PWD/src''${PYTHONPATH:+:$PYTHONPATH}
        '';
      };
    };
}
