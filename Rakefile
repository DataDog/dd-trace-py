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

desc 'CI dependent task; tasks in parallel'
task:ci_test do
  sh "docker-compose up -d | cat"
  begin
    case ENV['CIRCLE_NODE_INDEX'].to_i
    when 0
      sh "tox -e wait,py27-cassandra35,py27-cassandra36,py27-cassandra37,py34-cassandra35,py34-cassandra36,py34-cassandra37"
      sh "detox -e flake8,py27-tracer,py34-tracer,py27-integration,py34-integration,py27-contrib,py34-contrib,py27-bottle12-webtest,py34-bottle12-webtest"
    when 1
      sh "tox -e wait py27-elasticsearch23,py34-elasticsearch23,"
      sh "detox -e py27-falcon10,py34-falcon10,py27-django18-djangopylibmc06-djangoredis45-pylibmc-redis-memcached,py27-django19-djangopylibmc06-djangoredis45-pylibmc-redis-memcached,py27-django110-djangopylibmc06-djangoredis45-pylibmc-redis-memcached,py34-django18-djangopylibmc06-djangoredis45-pylibmc-redis-memcached,py34-django19-djangopylibmc06-djangoredis45-pylibmc-redis-memcached,py34-django110-djangopylibmc06-djangoredis45-pylibmc-redis-memcached,py27-flask010-blinker,py27-flask011-blinker,py34-flask010-blinker,py34-flask011-blinker"
      sh "detox -e py27-flask010-flaskcache012-memcached-redis-blinker,py27-flask011-flaskcache012-memcached-redis-blinker,py27-mysqlconnector21,py34-mysqlconnector21,py27-pylibmc140,py27-pylibmc150,py34-pylibmc140,py34-pylibmc150,py27-pymongo30-mongoengine,py27-pymongo31-mongoengine,py27-pymongo32-mongoengine,py27-pymongo33-mongoengine,py34-pymongo30-mongoengine,py34-pymongo31-mongoengine,py34-pymongo32-mongoengine,py34-pymongo33-mongoengine,py27-pyramid17-webtest,py27-pyramid18-webtest,py34-pyramid17-webtest,py34-pyramid18-webtest"
    when 2
      sh "tox -e wait,py27-sqlalchemy10-psycopg2,py27-sqlalchemy11-psycopg2,py34-sqlalchemy10-psycopg2,py34-sqlalchemy11-psycopg2,"
      sh "detox -e py27-flask010-flaskcache013-memcached-redis-blinker,py27-flask011-flaskcache013-memcached-redis-blinker,py34-flask010-flaskcache013-memcached-redis-blinker,py34-flask011-flaskcache013-memcached-redis-blinker,py27-gevent10,py27-gevent11,py34-gevent10,py34-gevent11"
      sh "detox -e py27-requests208,py27-requests209,py27-requests210,py27-requests211,py34-requests208,py34-requests209,py34-requests210,py34-requests211,py27-redis,py34-redis,py27-sqlite3,py34-sqlite3"
    else
      puts 'Too many workers than parallel tasks'
    end
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

  task :build => :clean do
    puts "building release in #{RELEASE_DIR}"
    sh "python setup.py -q sdist -d #{RELEASE_DIR}"
  end

  task :release => :build do
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
