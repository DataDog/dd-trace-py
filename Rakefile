# Dev commands
task :test do
  sh "python setup.py test"
end

task :dev do
  sh "pip uninstall -y ddtrace"
  sh "pip install -e ."
end

task :release do
  # Use mkwheelhouse to build the wheel, push it to S3 then update the repo index
  # If at some point, we need only the 2 first steps:
  #  - python setup.py bdist_wheel
  #  - aws s3 cp dist/*.whl s3://pypi.datadoghq.com/#{s3_dir}/
  s3_bucket = 'pypi.datadoghq.com'
  s3_dir = ENV['S3_DIR']
  fail "Missing environment variable S3_DIR" if !s3_dir or s3_dir.empty?

  sh "mkwheelhouse s3://#{s3_bucket}/#{s3_dir}/ ."
end

task :clean do
  sh 'rm -rf build *egg*'
end


task :docs do
  Dir.chdir 'docs' do
    sh "make html"
  end
end

task :'docs:loop' do
  # FIXME do something real here
  while true do
    sleep 2
    Rake::Task["docs"].execute
  end
end
