#!/usr/bin/perl
# The stand-in MCP server plugin/mcp.sh starts when trazo cannot serve:
# no per-user install and no checkout venv, or an incomplete install. A server that fails
# leaves only "Connection closed" in the app, and Claude Code caches the
# failure for 15 minutes, past setup and the new chat. This one connects,
# speaks the same stdio JSON-RPC as trazo.mcp and lists one tool,
# setup_status, which tells the agent to run the setup skill.
# The system Perl and its core modules only (macOS /usr/bin/perl is a real
# interpreter, not a Command Line Tools shim); needs no PATH, writes nothing.
# Usage: /usr/bin/perl mcp-standin.pl DATA_FOLDER, with $TRAZO_PLUGIN_ROOT set.
use strict;
use warnings;
use B ();
use JSON::PP ();

my @PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05");
# Paths stay bytes for the file system; JSON gets them decoded from UTF-8.
my $data = $ARGV[0] // "";
my $root = $ENV{TRAZO_PLUGIN_ROOT} // "";

my $wire = JSON::PP->new->utf8->canonical->allow_nonref;
# The tool result's text, laid out like json.dumps(indent=2) in trazo.mcp.
my $text = JSON::PP->new->canonical->allow_nonref->indent->indent_length(2)->space_after;

my $TOOL = {
    name => "setup_status",
    description => "Why trazo's tools are missing: trazo is not set up on this computer, or the installed "
        . "trazo is too old to serve them. Returns the install and `next`: \"setup\" or \"update\" (run "
        . "the trazo:setup skill), or \"restart\" (setup is done: a new chat loads the tools), and "
        . "`message`, what to tell the user, and `pandoc`, a Pandoc setup would use instead of downloading "
        . "one. `project` is ignored.",
    inputSchema => {
        type => "object",
        properties => {project => {type => "string",
                                   description => "absolute path of the paper folder (holds trazo.toml)"}},
        required => [],
        additionalProperties => JSON::PP::false,
    },
    annotations => {readOnlyHint => JSON::PP::true, destructiveHint => JSON::PP::false},
};

sub chars {
    my ($bytes) = @_;
    my $copy = $bytes;
    utf8::decode($copy);
    return $copy;
}

sub entries {
    my ($folder, $pattern) = @_;
    opendir(my $handle, $folder) or return ();
    my @found = sort grep { /$pattern/ && -d "$folder/$_" } readdir $handle;
    closedir $handle;
    return @found;
}

sub site_packages {
    my $lib = "$data/venv/lib";
    return map { "$lib/$_/site-packages" } entries($lib, qr/^python/);
}

# The same rule as trazo.machine.installed_version and plugin/session-start.sh:
# the name of the venv's one trazo-VERSION.dist-info folder; "" for none or more.
sub installed_version {
    return "" unless -x "$data/venv/bin/trazo";
    my @found = map { entries($_, qr/^trazo-.+\.dist-info$/) } site_packages();
    return @found == 1 ? chars($found[0] =~ s/^trazo-//r =~ s/\.dist-info$//r) : "";
}

# The install has trazo's MCP server.
sub install_serves {
    return -x "$data/venv/bin/trazo" && grep { -f "$_/trazo/mcp.py" } site_packages();
}

# Set up, as plugin/session-start.sh decides: an installed trazo and a
# non-empty machine.json, which `trazo setup` writes last.
sub set_up {
    my ($installed) = @_;
    return $installed ne "" && -s "$data/machine.json";
}

# A Pandoc `trazo setup` would use instead of downloading one, by its rule
# (trazo.tool_setup.existing_pandoc, machine.PANDOC_LOCATIONS): on PATH,
# then where Homebrew puts it.
sub existing_pandoc {
    for my $folder (split /:/, $ENV{PATH} // "") {
        return ("$folder/pandoc", "PATH") if $folder ne "" && -f "$folder/pandoc" && -x _;
    }
    for my $path ("/opt/homebrew/bin/pandoc", "/usr/local/bin/pandoc") {
        return ($path, "known location") if -f $path;
    }
    return ("", "");
}

sub plugin_version {
    open(my $handle, "<:raw", "$root/.claude-plugin/plugin.json") or return "";
    my $manifest = eval { local $/; $wire->decode(scalar <$handle>) };
    my $version = ref $manifest eq "HASH" ? $manifest->{version} : undef;
    return is_string($version) ? $version : "";
}

sub setup_status {
    my $installed = installed_version();
    my $plugin = $root ne "" ? plugin_version() : "";
    my ($next, $message);
    # plugin/mcp.sh would start the real server now: setup ran during this chat.
    if (set_up($installed) && (install_serves() || -x "$root/packages/trazo/.venv/bin/trazo")) {
        $next = "restart";
        $message = "trazo is now set up, but this chat started before setup and has only this stand-in "
            . "server. Tell the user to start a new chat to load all the trazo tools.";
    } elsif (set_up($installed)) {
        $next = "update";
        $message = "trazo $installed is installed but cannot run the plugin's MCP server, so only this "
            . "stand-in server runs. Run the trazo:setup skill: it reinstalls trazo from the plugin's "
            . "files and downloads only what is missing. After setup, a new chat loads "
            . "all the trazo tools.";
    } elsif ($installed ne "" && install_serves()) {  # so machine.json is missing or empty
        $next = "setup";
        $message = "trazo setup did not finish on this computer (trazo is installed but its machine file "
            . "is missing, for example after a failed download), so only this stand-in server runs. Run the "
            . "trazo:setup skill again: it downloads only what is missing. "
            . "Once it finishes, a new chat loads all the trazo tools.";
    } else {
        $next = "setup";
        $message = "trazo is not set up on this computer, so only this stand-in server runs. Run the "
            . "trazo:setup skill: the user's request for trazo or paper work is the go-ahead. "
            . "After setup, a new chat loads all the trazo tools.";
    }
    my ($pandoc, $where) = existing_pandoc();
    return {
        plugin => {root => $root ne "" ? chars($root) : undef, version => $plugin ne "" ? $plugin : undef},
        install => {folder => chars($data), installed => $installed ne "" ? JSON::PP::true : JSON::PP::false,
                    version => $installed ne "" ? $installed : undef,
                    matches_plugin => $installed ne "" && $plugin ne ""
                        ? ($installed eq $plugin ? JSON::PP::true : JSON::PP::false) : undef},
        pandoc => {found => $pandoc ne "" ? JSON::PP::true : JSON::PP::false,
                   path => $pandoc ne "" ? chars($pandoc) : undef, where => $where ne "" ? $where : undef},
        next => $next,
        message => $message,
    };
}

sub flags {
    return B::svref_2object(\$_[0])->FLAGS;
}

# A JSON string, not a number or boolean (JSON::PP keeps the scalar's flags).
sub is_string {
    my ($value) = @_;
    return defined $value && !ref $value && (flags($value) & B::SVp_POK)
        && !(flags($value) & (B::SVp_IOK | B::SVp_NOK));
}

# The message's id when it is a valid one (string or integer), else undef.
sub request_id {
    my ($message) = @_;
    return undef unless ref $message eq "HASH" && defined $message->{id} && !ref $message->{id};
    my $id = $message->{id};
    my $flags = flags($id);
    return $id if is_string($id) || (($flags & B::SVp_IOK) && !($flags & (B::SVp_POK | B::SVp_NOK)));
    return undef;
}

sub send_message {
    my ($message) = @_;
    print STDOUT $wire->encode({jsonrpc => "2.0", %$message}), "\n";
}

sub send_error {
    my ($id, $code, $message) = @_;
    send_message({id => $id, error => {code => $code, message => $message}});
}

# json.dumps(indent=2) ends without a newline.
sub result_text {
    my $encoded = $text->encode($_[0]);
    chomp $encoded;
    return $encoded;
}

sub call_tool {
    my ($id) = @_;
    my $reply;
    my $result = eval { setup_status() };
    if ($result) {
        $reply = {content => [{type => "text", text => result_text($result)}], structuredContent => $result,
                  isError => JSON::PP::false};
    } else {
        $reply = {content => [{type => "text", text => "setup_status failed: $@"}], isError => JSON::PP::true};
    }
    send_message({id => $id, result => $reply});
}

sub handle {
    my ($message) = @_;
    my $is_hash = ref $message eq "HASH";
    return if $is_hash && !exists $message->{method} && (exists $message->{result} || exists $message->{error});
    my $id = request_id($message);
    if (!$is_hash || !is_string($message->{jsonrpc}) || $message->{jsonrpc} ne "2.0"
            || !is_string($message->{method}) || (exists $message->{id} && !defined $id)) {
        send_error($id, -32600, "Invalid Request");
        return;
    }
    return unless exists $message->{id};  # a notification: no reply
    my $method = $message->{method};
    my $params = exists $message->{params} ? $message->{params} : {};
    if (ref $params ne "HASH") {
        send_error($id, -32602, "params must be an object");
    } elsif ($method eq "initialize") {
        my $asked = $params->{protocolVersion};
        my $known = is_string($asked) && grep { $_ eq $asked } @PROTOCOLS;
        my $plugin = $root ne "" ? plugin_version() : "";
        send_message({id => $id, result => {
            protocolVersion => $known ? $asked : $PROTOCOLS[0],
            capabilities => {tools => {}},
            serverInfo => {name => "trazo", version => $plugin ne "" ? $plugin : "0"},
            instructions => "trazo is not set up on this computer, or the installed trazo is too old: only "
                . "setup_status is available. Call it, then run the trazo:setup skill when the user asks "
                . "for trazo or paper work."}});
    } elsif ($method eq "ping") {
        send_message({id => $id, result => {}});
    } elsif ($method eq "tools/list") {
        send_message({id => $id, result => {tools => [$TOOL]}});
    } elsif ($method eq "tools/call") {
        my ($name, $args) = ($params->{name}, $params->{arguments});
        $args = {} if !$args || (ref $args eq "ARRAY" && !@$args);  # trazo.mcp's `or {}`
        if (!is_string($name) || $name ne "setup_status") {
            send_error($id, -32602, "Unknown tool: " . (is_string($name) ? $name : $wire->encode($name)));
        } elsif (ref $args ne "HASH") {
            send_error($id, -32602, "arguments must be an object");
        } else {
            call_tool($id);
        }
    } else {
        send_error($id, -32601, "Method not found: $method");
    }
}

$| = 1;
binmode STDIN;
binmode STDOUT;
print STDERR setup_status()->{next} eq "update"
    ? "trazo is too old to run the MCP server; ask the agent to update it\n"
    : "trazo is not set up; ask the agent to set it up\n";
while (my $line = <STDIN>) {
    next unless $line =~ /\S/;
    my $message = eval { $wire->decode($line) };
    if ($@) {
        send_error(undef, -32700, "Parse error");
        next;
    }
    next if eval { handle($message); 1 };
    my $failure = $@;
    my $id = request_id($message);
    if (defined $id && exists $message->{method}) {
        send_error($id, -32603, "Internal error: $failure");
    } else {
        print STDERR "trazo stand-in: $failure";
    }
}
exit 0;
