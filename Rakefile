# Dev commands
task :test do
  sh "python setup.py test"
end

task :dev do
  sh "pip uninstall -y ddtrace"
  sh "pip install -e ."
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


# Deploy tasks
S3_BUCKET = 'pypi.datadoghq.com'
S3_DIR = ENV['S3_DIR']

task :release_wheel do
  # Use mkwheelhouse to build the wheel, push it to S3 then update the repo index
  # If at some point, we need only the 2 first steps:
  #  - python setup.py bdist_wheel
  #  - aws s3 cp dist/*.whl s3://pypi.datadoghq.com/#{s3_dir}/
  fail "Missing environment variable S3_DIR" if !S3_DIR or S3_DIR.empty?

  sh "mkwheelhouse s3://#{S3_BUCKET}/#{S3_DIR}/ ."
end

task :release_docs do
  # Build the documentation then it to S3
  Dir.chdir 'docs' do
    sh "make html"
  end
  fail "Missing environment variable S3_DIR" if !S3_DIR or S3_DIR.empty?

  sh "aws s3 cp --recursive docs/_build/html/ s3://#{S3_BUCKET}/#{S3_DIR}/docs/"
end
