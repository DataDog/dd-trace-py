# Dev commands
task :test do
  sh "python setup.py test"
end

task :dev do
  sh "pip uninstall -y ddtrace"
  sh "pip install -e ."
end

# CI and deploy commands
namespace :ci do
  namespace :dev do
    task :build do
      branch = ENV['CIRCLE_BRANCH']
      build_number = ENV['CIRCLE_BUILD_NUM']
      ENV['VERSION_SUFFIX'] = "#{branch}#{build_number}"
      sh "python setup.py bdist_wheel"
    end

    task :push_package do
      sh "aws s3 cp dist/*.whl s3://pypi.datadoghq.com/apm_dev/"
    end

    task :release => [:build, :push_package]
  end

  namespace :unstable do
    task :build do
      sh "python setup.py bdist_wheel"
    end

    task :push_package do
      sh "aws s3 cp dist/*.whl s3://pypi.datadoghq.com/apm_unstable/"
    end

    task :release => [:build, :push_package]
  end
end
