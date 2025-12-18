# Uninstall ddtrace from the venv
pip uninstall -y ddtrace

# Remove build/
rm -rf build/

# Remove built shared objects
find . -name "*.so" -print -delete

