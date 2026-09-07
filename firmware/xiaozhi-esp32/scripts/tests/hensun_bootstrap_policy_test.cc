// Host-only policy vectors. Not executed by the Python source-contract suite.
#include "../../main/hensun_bootstrap_policy.h"

#include <cassert>

int main() {
    const std::string trusted = "https://api.example.test/v1/device/xiaozhi-bootstrap";
    assert(HensunSameBootstrapOrigin(trusted, trusted));
    assert(HensunSameBootstrapOrigin("HTTPS://API.EXAMPLE.TEST:443/other", trusted));
    assert(!HensunSameBootstrapOrigin("http://api.example.test/path", trusted));
    assert(!HensunSameBootstrapOrigin("https://api.example.test:444/path", trusted));
    assert(!HensunSameBootstrapOrigin("https://api.example.test.evil.test/path", trusted));
    assert(!HensunSameBootstrapOrigin("https://api.example.test@evil.test/path", trusted));
    assert(!HensunSameBootstrapOrigin("https://user@api.example.test/path", trusted));
    assert(!HensunSameBootstrapOrigin("https://api.example.test\\@evil.test/path", trusted));
    assert(!HensunSameBootstrapOrigin("https://api.example.test\r\nHost: evil.test/path", trusted));
    assert(!HensunSameBootstrapOrigin("https://api.example.test%2fevil/path", trusted));
    assert(!HensunSameBootstrapOrigin("https://api.example.test:0/path", trusted));
    assert(!HensunSameBootstrapOrigin("https://api.example.test:65536/path", trusted));
    assert(!HensunSameBootstrapOrigin("https://api.example.test:/path", trusted));
    assert(!HensunSameBootstrapOrigin("https://api.example.test:+443/path", trusted));
    assert(!HensunSameBootstrapOrigin("//api.example.test/path", trusted));
    assert(!HensunSameBootstrapOrigin("", ""));
    const std::string local = "http://192.168.2.98:8000/v1/device/xiaozhi-bootstrap";
    assert(HensunSameBootstrapOrigin("http://192.168.2.98:8000/activate", local));
    assert(!HensunSameBootstrapOrigin("http://192.168.2.98/activate", local));
    assert(HensunSameBootstrapOrigin("HTTP://LOCALHOST:80/path", "http://localhost/bootstrap"));
    return 0;
}
