cd ../
rm -rf temp_ubsan
mkdir -p temp_ubsan
cd temp_ubsan

cp ../project/ubsan.c .
cp ../project/ubsan_setup.py setup.py

# Build in-place using your instrumented Python and flags
CC=gcc CFLAGS="-fsanitize=undefined -O1 -fno-omit-frame-pointer -g" \
    LDFLAGS="-fsanitize=undefined" \
    $(which python3.10) -m pip install --no-binary :all: .

export UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1:verbosity=1:report_error_type=1:"
python3.10 -c "import ubsantest; ubsantest.overflow()"

echo "Finished testing!"