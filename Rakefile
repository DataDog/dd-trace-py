
task :build do
  sh "pip wheel ./"
end

task :test do
  sh "python setup.py test"
end

task :install => :build do
  sh "pip install *.whl"
end

task :upgrade => :build do
  sh "pip install -U *.whl"
end

task :clean do
  sh "python setup.py clean"
  sh "rm -rf *.whl dist *.egg-info build"
end

task :ci => [:clean, :test, :build]
