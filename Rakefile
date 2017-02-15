desc "Starts all backing services and run all tests"
task :test do
  sh "docker-compose up -d | cat"
  begin
    sh "tox"
  ensure
    sh "docker-compose kill"
  end
  sh "python -m tests.benchmark"
end

desc "Run tests with envs matching the given pattern."
task :"test:envs", [:grep] do |t, args|
  pattern = args[:grep]
  if !pattern
    puts 'specify a pattern like rake test:envs["py27.*mongo"]'
  else
    sh "tox -l | grep '#{pattern}' | xargs tox -e"
  end
end

namespace :docker do
  task :up do
    sh "docker-compose up -d | cat"
  end

  task :down do
    sh "docker-compose down"
  end
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
  sh "pip install sphinx"
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

desc "release the a new wheel"
task :'release:wheel' do
  # Use mkwheelhouse to build the wheel, push it to S3 then update the repo index
  # If at some point, we need only the 2 first steps:
  #  - python setup.py bdist_wheel
  #  - aws s3 cp dist/*.whl s3://pypi.datadoghq.com/#{s3_dir}/
  fail "Missing environment variable S3_DIR" if !S3_DIR or S3_DIR.empty?

  sh "mkwheelhouse s3://#{S3_BUCKET}/#{S3_DIR}/ ."
end

desc "release the docs website"
task :'release:docs' => :docs do
  fail "Missing environment variable S3_DIR" if !S3_DIR or S3_DIR.empty?
  sh "aws s3 cp --recursive docs/_build/html/ s3://#{S3_BUCKET}/#{S3_DIR}/docs/"
end

namespace :pypi do
  RELEASE_DIR = '/tmp/dd-trace-py-release'

  task :clean do
    FileUtils.rm_rf(RELEASE_DIR)
  end

  task :install do
    sh 'pip install twine'
  end

  task :build => :clean do
    puts "building release in #{RELEASE_DIR}"
    sh "python setup.py -q sdist -d #{RELEASE_DIR}"
  end

  task :release => [:install, :build] do
    builds = Dir.entries(RELEASE_DIR).reject {|f| f == '.' || f == '..'}
    if builds.length == 0
        fail "no build found in #{RELEASE_DIR}"
    elsif builds.length > 1
        fail "multiple builds found in #{RELEASE_DIR}"
    end

    build = "#{RELEASE_DIR}/#{builds[0]}"

    puts "uploading #{build}"
    sh "twine upload #{build}"
  end
end

namespace :version do

  def get_version()
    return `python setup.py --version`.strip()
  end

  def set_version(old, new)
    branch = `git name-rev --name-only HEAD`.strip()
    if  branch != "master"
      puts "you should only tag the master branch"
      return
    end
    msg = "bumping version #{old} => #{new}"
    path = "ddtrace/__init__.py"
    sh "sed -i 's/#{old}/#{new}/' #{path}"
    sh "git commit -m '#{msg}' #{path}"
    sh "git tag v#{new}"
    puts "Verify everything looks good, then `git push && git push --tags`"
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

  desc "Cut a new bugfix release"
  task :bugfix do
    inc_version("bugfix")
  end

  desc "Cut a new minor release"
  task :minor do
    inc_version("minor")
  end

  task :major do
    inc_version("major")
  end

end
