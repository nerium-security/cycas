# Create future branch
PREFIX='feature'
SUFFIX='azurefunctions'
git switch -c "$PREFIX/$SUFFIX"

< Do the actual work >

# push to main
git add .
git commit -m "Fix: $SUFFIX"
git push -u origin "$PREFIX/$SUFFIX"

git switch main