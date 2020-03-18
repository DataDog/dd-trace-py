# Deploy tasks
namespace :pypi do
  RELEASE_DIR = './dist/'

  def get_version()
    return `python setup.py --version`.strip
  end

  def get_branch()
    return `git name-rev --name-only HEAD`.strip
  end

  task :confirm do
    ddtrace_version = get_version

    if get_branch.downcase != 'tags/v#{ddtrace_version}'
      print "WARNING: Expected current commit to be tagged as 'tags/v#{ddtrace_version}, instead we are on '#{get_branch}', proceed anyways [y|N]? "
      $stdout.flush

      abort if $stdin.gets.to_s.strip.downcase != 'y'
    end

    puts "WARNING: This task will build and release new wheels to https://pypi.org/project/ddtrace/, this action cannot be undone"
    print "         To proceed please type the version '#{ddtrace_version}': "
    $stdout.flush

    abort if $stdin.gets.to_s.strip.downcase != ddtrace_version
  end

  task :clean do
    FileUtils.rm_rf(RELEASE_DIR)
  end

  task :install do
    sh 'pip install twine'
  end

  task :build => :clean do
    puts "building release in #{RELEASE_DIR}"
    sh "scripts/build-dist"
  end

  task :release => [:confirm, :install, :build] do
    builds = Dir.entries(RELEASE_DIR).reject {|f| f == '.' || f == '..'}
    if builds.length == 0
        fail "no build found in #{RELEASE_DIR}"
    end

    puts "uploading #{RELEASE_DIR}/*"
    sh "twine upload #{RELEASE_DIR}/*"
  end
end
