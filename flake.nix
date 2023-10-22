{
  description = "Development environment for dd-trace-py";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs?ref=nixos-unstable";
    nixpkgs22.url = "github:NixOS/nixpkgs?ref=22.11";
  };

  outputs = { self, nixpkgs, nixpkgs22 }:
    let
      allSystems =
        [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];

      forAllSystems = f:
        nixpkgs.lib.genAttrs allSystems (system:
          f {
            pkgs = import nixpkgs { inherit system; };
            pkgs22 = import nixpkgs22 { inherit system; };
          });
    in {
      devShells = forAllSystems ({ pkgs, pkgs22 }: {
        default = let python = pkgs.python311;
        in pkgs.mkShell {
          buildInputs = [
            (python.withPackages (ps: with ps; [ virtualenv ]))
            pkgs.python310
            pkgs.python39
            pkgs.python38
            pkgs22.python37
          ];
          shellHook = ''
            if [[ ! -d .venv ]]; then
              echo "Creating a new virtual env..."
              virtualenv -p 3.11 .venv
            fi
            source .venv/bin/activate
            pip install -q --disable-pip-version-check hatch hatch-containers
            echo "Nix development shell loaded."
          '';
        };
      });
    };
}
