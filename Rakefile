
desc "run tests"
task :test do
  sh "python setup.py test"
end

desc "install the library in dev mode"
task :dev do
  sh "pip uninstall -y ddtrace"
  sh "pip install -e ."
end

desc "remove artifacts"
task :clean do
  sh 'python setup.py clean'
  sh 'rm -rf build *egg* *.whl dist'
end

desc "build the docs"
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

namespace :release do

  desc "release the a new wheel"
  task :wheel do
    # Use mkwheelhouse to build the wheel, push it to S3 then update the repo index
    # If at some point, we need only the 2 first steps:
    #  - python setup.py bdist_wheel
    #  - aws s3 cp dist/*.whl s3://pypi.datadoghq.com/#{s3_dir}/
    fail "Missing environment variable S3_DIR" if !S3_DIR or S3_DIR.empty?

    sh "mkwheelhouse s3://#{S3_BUCKET}/#{S3_DIR}/ ."
  end


  desc "release the docs website"
  task :docs => :docs do
    fail "Missing environment variable S3_DIR" if !S3_DIR or S3_DIR.empty?
    sh "aws s3 cp --recursive docs/_build/html/ s3://#{S3_BUCKET}/#{S3_DIR}/docs/"
  end

end


namespace :version do

  def get_version()
    return `python setup.py --version`.strip()
  end

  def set_version(old, new)
    branch = `git name-rev --name-only HEAD`.strip()
    # if  branch != "master"
    #   puts "you should only tag the master branch"
    #   return
    # end
    msg = "bumping version #{old} => #{new}"
    puts msg

    path = "ddtrace/__init__.py"

    sh "sed -i 's/#{old}/#{new}/' #{path}"
    sh "git commit -m '#{msg}' #{path}"
    sh "git tag v#{new}"
  end

  def inc_version_num(version, type)
    split = version.split(".").map{|v| v.to_i}
    if type == 'bugfix'
      split[2] += 1
    elsif type == 'minor'
       split[1] += 1
       split[2] = 0
    elsif type == 'major'
       split[0] += 1
       split[1] = 0
       split[2] = 0
    end
    return split.join(".")
  end

  def inc_version(type)
    old = get_version()
    new = inc_version_num(old, type)
    set_version(old, new)
  end

  task :bugfix do
    inc_version("bugfix")
  end

  task :minor do
    inc_version("minor")
  end

  task :major do
    inc_version("major")
  end

end


