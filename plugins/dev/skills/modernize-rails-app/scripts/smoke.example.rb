# Usage: ruby smoke.rb PORT OUTDIR
# Signs in on a running app server and saves a set of pages. Adapt the host,
# the credentials and the page list to the app.
require "net/http"
require "uri"
require "fileutils"

port, outdir = ARGV[0].to_i, ARGV[1]
FileUtils.mkdir_p(outdir)

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 6_0 like Mac OS X) AppleWebKit/536.26 (KHTML, like Gecko) Version/6.0 Mobile/10A5376e Safari/8536.25"
@cookies = {}
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

def request(method, path, host: "localhost", ua: nil, form: nil, headers: {})
  http = Net::HTTP.new("127.0.0.1", $port)
  klass = { get: Net::HTTP::Get, post: Net::HTTP::Post, put: Net::HTTP::Put, delete: Net::HTTP::Delete }[method]
  req = klass.new(path)
  req["Host"] = "#{host}:#{$port}"
  req["User-Agent"] = ua if ua
  req["Cookie"] = @cookies.map { |k, v| "#{k}=#{v}" }.join("; ") unless @cookies.empty?
  headers.each { |k, v| req[k] = v }
  req.set_form_data(form) if form
  res = http.request(req)
  Array(res.get_fields("set-cookie")).each do |c|
    k, v = c.split(";").first.split("=", 2)
    @cookies[k] = v
  end
  res
end

def normalize(body)
  body.to_s
    .gsub(/name="authenticity_token" type="hidden" value="[^"]*"/, 'AUTH')
    .gsub(/type="hidden" name="authenticity_token" value="[^"]*"/, 'AUTH')
    .gsub(/name="authenticity_token" value="[^"]*" type="hidden"/, 'AUTH')
    .gsub(/<meta (content="[^"]*" )?name="csrf-[a-z]+"( content="[^"]*")? ?\/?>/, '')
    .gsub(/authenticity_token=[^&'"]*/, 'authenticity_token=X')
    .gsub(/(authenticity_token&quot;\); i\.setAttribute\(&#39;value&#39;, &quot;)[^&]*/, '\1X')
    .gsub(/(authenticity_token=X' \+ encodeURIComponent\(')[^']*/, '\1X')
    .gsub(/\?\d{10}/, "?TS")
end

$port = port
login_page = request(:get, "/session/new", headers: { "Accept" => BROWSER_ACCEPT })
token = login_page.body[/name="authenticity_token" (?:type="hidden" )?value="([^"]*)"/, 1]
res = request(:post, "/session", form: { "username" => "user", "password" => "password", "authenticity_token" => token.to_s }, headers: { "Accept" => BROWSER_ACCEPT })
File.write("#{outdir}/_login.txt", "#{res.code} #{res["location"]}\n")
# Without these checks, the captures of two runs that both fail to sign in
# can match. Adapt them to the app's successful sign-in response and a signed-in page.
abort "sign-in failed: POST /session returned #{res.code} #{res["location"]}" unless res.is_a?(Net::HTTPRedirection) && !res["location"].to_s.include?("/session")
account = request(:get, "/account", headers: { "Accept" => BROWSER_ACCEPT })
abort "sign-in failed: GET /account returned #{account.code} #{account["location"]}" unless account.is_a?(Net::HTTPSuccess)

pages = {
  "posts" => "/posts",
  "post_1" => "/posts/1",
  "post_new" => "/posts/new",
  "post_1_edit" => "/posts/1/edit",
  "account" => "/account",
  "session_new" => "/session/new",
  "up" => "/up",
  "missing_post" => "/posts/999999",
  "missing_route" => "/nope/nothing",
}

pages.each do |name, path|
  [[name, nil], ["#{name}.iphone", IPHONE]].each do |label, ua|
    res = request(:get, path, ua: ua, headers: { "Accept" => BROWSER_ACCEPT })
    File.write("#{outdir}/#{label}.html", "#{res.code} #{res["content-type"]} #{res["location"]}\n#{normalize(res.body)}")
  end
end

%w[posts.rss].each do |path|
  res = request(:get, "/#{path}")
  File.write("#{outdir}/#{path.tr("/", "_")}.xml", "#{res.code} #{res["content-type"]}\n#{res.body}")
end
