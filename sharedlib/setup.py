from setuptools import setup, find_packages

setup(
    name='sharedlib',
    version='0.3.0',
    description='Shared library',
    packages=find_packages(),
    install_requires=[],
    include_package_data=True,
    zip_safe=False
)