.. Toffee documentation master file, created by
   sphinx-quickstart on Fri Mar 14 11:59:30 2025.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

Toffee documentation
====================

**Toffee** 是使用 Python 语言编写的一套硬件验证框架，它依赖于多语言转换工具 `Picker <https://github.com/XS-MLVP/picker>`_ ，该工具能够将硬件设计的 Verilog 代码转换为 Python Package，使得用户可以使用 Python 来驱动并验证硬件设计。

其吸收了部分 UVM 验证方法学，以保证验证环境的规范性和可复用性，并重新设计了整套验证环境的搭建方式，使其更符合软件领域开发者的使用习惯，从而使软件开发者可以轻易地上手硬件验证工作。

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   quick-start
   canonical_env
   env/index
   cases/index
   verification
   api/toffee
