"""仅供浏览器回归测试的固定响应服务，不使用项目数据库或真实模型。"""
import os
from test_automatic_evidence import EvidenceTests
os.environ['ADMIN_TOKEN'] = 'test-only-token'
fixture = EvidenceTests()
fixture.setUp()
fixture.populate(('实习', '校招'))
from careersafari.main import app
