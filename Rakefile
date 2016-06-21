
task :build do
  sh "pip wheel ./"
end

task :test do
  sh "python setup.py test"
end

task :install do
  sh "pip install *.whl"
end

task :upgrade do
  sh "pip install -U *.whl"
end

task :clean do
  sh "python setup.py clean"
  sh "rm -rf *.whl dist *.egg-info build"
end

task :upload do
  sh "s3cmd put ddtrace-*.whl s3://pypi.datadoghq.com/"
end

task :ci => [:clean, :test, :build]

task :release => [:ci, :upload]

task :default => :test
